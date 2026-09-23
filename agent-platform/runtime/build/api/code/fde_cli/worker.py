"""Executed inside the existing API container over administrator SSH, never an HTTP endpoint."""
import os
import copy
import hashlib
import json

ACCOUNT_ID = ''
WORKSPACES = {'fde': os.environ.get('FDE_CLI_WORKSPACE_ID', '')}


def merge(base, patch):
    """Objects merge recursively; arrays replace explicitly."""
    result = copy.deepcopy(base)
    for key, value in patch.items():
        result[key] = merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else copy.deepcopy(value)
    return result


def revision(state):
    content = {k: state.get(k) for k in ('agent_soul', 'active_config_snapshot', 'draft')}
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def execute(req, *, account_id=ACCOUNT_ID, allowed_workspaces=None, allowed_roles=('owner', 'admin'), app_instance=None):
    if app_instance is None:
        from app import app
    else:
        app = app_instance
    from core.db.session_factory import session_factory
    from sqlalchemy import select
    from models import Account, Tenant, TenantAccountJoin, App
    from models.agent import Agent, AgentStatus, AgentScope, AgentSource, AgentConfigDraft, AgentConfigDraftType
    from services.app_service import AppService, CreateAppParams
    from services.agent.roster_service import AgentRosterService
    from services.agent.composer_service import AgentComposerService as Composer
    from services.entities.agent_entities import ComposerSavePayload
    from models.agent_config_entities import AgentSoulConfig

    workspace = WORKSPACES.get(req['workspace'], req['workspace'])
    if workspace not in (allowed_workspaces if allowed_workspaces is not None else WORKSPACES.values()):
        raise ValueError('仅允许配置中明确授权的 fde / sales 工作空间')
    with app.app_context(), session_factory.create_session() as session:
        account = session.get(Account, account_id)
        tenant = session.get(Tenant, workspace)
        membership = session.scalar(select(TenantAccountJoin).where(
            TenantAccountJoin.account_id == account_id, TenantAccountJoin.tenant_id == workspace))
        if not account or str(account.status) != 'active' or not tenant or not membership or str(membership.role) not in allowed_roles:
            raise PermissionError('当前账号没有此工作空间要求的有效编辑权限')
        account.set_current_tenant_with_session(tenant, session=session)
        common = dict(session=session, tenant_id=workspace)
        action = req['action']
        if action == 'doctor':
            return dict(ok=True, workspace_id=workspace, workspace_name=tenant.name, account_id=account.id, role=str(membership.role), transport='administrator-ssh')
        if action == 'list':
            rows = session.scalars(select(Agent).where(Agent.tenant_id == workspace, Agent.scope == AgentScope.ROSTER,
                Agent.source == AgentSource.AGENT_APP, Agent.status == AgentStatus.ACTIVE).order_by(Agent.created_at.desc()).limit(200)).all()
            return dict(agents=[dict(id=a.id, name=a.name, app_id=a.app_id, published=bool(a.active_config_is_published), snapshot_id=a.active_config_snapshot_id) for a in rows], limit=200)
        if action == 'create':
            spec = req['spec']
            if set(spec) - {'name', 'description', 'role', 'agent_soul'}:
                raise ValueError('spec 只接受 name / description / role / agent_soul')
            name = spec.get('name', '').strip()
            if not name or len(name) > 255:
                raise ValueError('name 必须为 1–255 字符')
            soul = AgentSoulConfig.model_validate(spec.get('agent_soul', {}))
            payload = ComposerSavePayload(variant='agent_app', save_strategy='save_to_current_version', agent_soul=soul)
            from services.agent.composer_validator import ComposerConfigValidator
            ComposerConfigValidator.validate_draft_save_payload(payload)
            Composer.validate_knowledge_datasets(**common, agent_soul=soul)
            existing = session.scalar(select(Agent.id).where(Agent.tenant_id == workspace, Agent.name == name, Agent.status == AgentStatus.ACTIVE))
            if existing:
                raise ValueError('同名 Agent 已存在：' + existing + '。请使用 configure，不重复创建。')
            if req.get('dry_run'):
                return dict(dry_run=True, workspace_id=workspace, name=name, validated=True)
            app_model = AppService().create_app(workspace, CreateAppParams(name=name, description=spec.get('description', ''), agent_role=spec.get('role', ''), mode='agent'), account, session=session)
            agent = AgentRosterService(session).get_app_backing_agent(tenant_id=workspace, app_id=app_model.id)
            # create_app commits by design; return the created id if subsequent configuration fails.
            req['created_agent_id'] = agent.id
            state = Composer.save_agent_composer(**common, agent_id=agent.id, account_id=account_id, payload=payload)
        else:
            agent = session.scalar(select(Agent).where(Agent.id == req['id'], Agent.tenant_id == workspace,
                Agent.scope == AgentScope.ROSTER, Agent.source == AgentSource.AGENT_APP, Agent.status == AgentStatus.ACTIVE).with_for_update())
            if not agent:
                raise ValueError('此工作空间内不存在该 Agent BETA')
            # Serialize edits against the normal draft; never consume a browser Build draft.
            session.scalars(select(AgentConfigDraft).where(AgentConfigDraft.agent_id == agent.id,
                AgentConfigDraft.tenant_id == workspace, AgentConfigDraft.draft_type == AgentConfigDraftType.DRAFT).with_for_update()).all()
            state = Composer.load_agent_composer(**common, agent_id=agent.id)
            if action in ('configure', 'publish') and req.get('revision') != revision(state):
                raise ValueError('版本不匹配：先 get 获取最新 revision，再审核并重试')
            if action == 'configure':
                soul = AgentSoulConfig.model_validate(merge(state['agent_soul'], req['patch']))
                payload = ComposerSavePayload(variant='agent_app', save_strategy='save_to_current_version', agent_soul=soul)
                from services.agent.composer_validator import ComposerConfigValidator
                ComposerConfigValidator.validate_draft_save_payload(payload)
                Composer.validate_knowledge_datasets(**common, agent_soul=soul)
                if req.get('dry_run'):
                    return dict(dry_run=True, validated=True, id=agent.id, agent_soul=soul.model_dump(mode='json'), revision=revision(state))
                state = Composer.save_agent_composer(**common, agent_id=agent.id, account_id=account_id, payload=payload)
            elif action == 'publish':
                if req.get('dry_run'):
                    from services.agent.composer_validator import ComposerConfigValidator
                    soul = AgentSoulConfig.model_validate(state['agent_soul'])
                    ComposerConfigValidator.validate_publish_payload(ComposerSavePayload(variant='agent_app', save_strategy='save_as_new_version', agent_soul=soul))
                    if not soul.model:
                        raise ValueError('请先配置模型')
                    Composer.validate_knowledge_datasets(**common, agent_soul=soul)
                    return dict(dry_run=True, id=agent.id, revision=revision(state), configuration_valid=True)
                Composer.publish_agent_app_draft(**common, agent_id=agent.id, account_id=account_id, version_note=req.get('note'))
                state = Composer.load_agent_composer(**common, agent_id=agent.id)
            elif action != 'get':
                raise ValueError('不支持的操作')
        if action != 'get':
            session.commit()
            state = Composer.load_agent_composer(**common, agent_id=agent.id)
        return dict(id=agent.id, app_id=agent.app_id, name=agent.name, workspace_id=workspace,
            published=state['active_config_is_published'], revision=revision(state),
            snapshot_id=agent.active_config_snapshot_id, agent_soul=state['agent_soul'],
            url=os.environ.get('CONSOLE_WEB_URL', '').rstrip('/') + '/agents/' + agent.id + '/configure')

import json
from pathlib import Path

from sales_backend.domain.business_options import business_options, OPPORTUNITY_GRADES
from sales_backend.domain.opportunities import prepare_change, stage_for_probability
from sales_backend.release_identity import release_identity


def test_frontend_fixture_is_the_actual_server_contract_and_stages_are_accepted():
    root = Path(__file__).resolve().parents[2]
    assert json.loads((root/'frontend/tests/helpers/business-options.json').read_text()) == business_options()
    for stage in business_options()['opportunity']['stages']:
        if stage['status'] != 'open':
            continue
        assert stage_for_probability(stage['probability']) == stage['code']
        result=prepare_change(None,{'name':'阶段验收','amount':100,'status':'open','probability':stage['probability']})
        assert result['probability'] == stage['probability']
    assert [g['min'] for g in OPPORTUNITY_GRADES] == [1000000,500000,100000,0]


def test_revision_is_explicit_and_invalid_or_missing_revision_never_claims_a_release(tmp_path):
    assert release_identity(tmp_path)['mode'] == 'development'
    (tmp_path/'REVISION').write_text('not-a-commit\n')
    assert release_identity(tmp_path)['revision'] is None
    (tmp_path/'REVISION').write_text('a'*40+'\n')
    migrations=tmp_path/'database/migrations';migrations.mkdir(parents=True)
    (migrations/'V118__targets.sql').touch()
    assert release_identity(tmp_path) == {'mode':'release','revision':'a'*40,'release':tmp_path.name,'expected_schema':'V118'}

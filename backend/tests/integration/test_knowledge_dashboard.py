"""Real authorized dashboard aggregates and drill-down share one material set."""
from uuid import UUID

import pytest

from .knowledge_fixtures import catalog_values, selection
from .knowledge_fixtures import kb as kb
from .test_knowledge_import import headers, payload

PREFIX = '/api/v1/knowledge'


@pytest.mark.asyncio
async def test_dashboard_source_attribution_scope_and_cumulative_beijing_days(kb):
    async with kb.client() as client:
        result = await client.post(PREFIX + '/imports/feishu', json=payload(kb), headers=headers())
        assert result.status_code == 200, result.text
        ids = [UUID(item['material_id']) for item in result.json()['items']]
        # An old imported material is part of the initial baseline, not a new
        # material on every version or attachment. Other row is created today.
        await kb.admin.execute(
            "UPDATE knowledge.material SET created_at=transaction_timestamp()-interval '45 days' WHERE id=$1",
            ids[0],
        )
        response = await client.get(PREFIX + '/stats')
        assert response.status_code == 200, response.text
        stats = response.json()
        assert stats['time_zone'] == 'Asia/Shanghai'
        assert {k: stats['metrics'][k]['count'] for k in ('total', 'external', 'contributors', 'mine')} == {
            'total': 2, 'external': 1, 'contributors': 1, 'mine': 0,
        }
        assert {v['label'] for v in stats['contributions']} == {'原作者', '未填写'}
        assert all(v['contributor_user_id'] is None for v in stats['contributions'])
        assert len(stats['trend']) == 30
        assert stats['trend'][0]['count'] == 1
        assert stats['trend'][0]['external_count'] == 1
        assert stats['trend'][-1]['count'] == 2
        today = await kb.admin.fetchval(
            "SELECT to_char(transaction_timestamp() AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD')"
        )
        assert stats['trend'][-1]['date'] == today
        for filters, expected in [({'external': 'true'}, 1), ({'mine': 'true'}, 0),
                                  ({'contributor_code': 'provider:原作者'}, 1),
                                  ({'contributor_code': 'provider:unknown'}, 1)]:
            listed = await client.get(PREFIX + '/materials', params=filters)
            counted = await client.get(PREFIX + '/stats', params=filters)
            assert listed.status_code == counted.status_code == 200
            assert listed.json()['total'] == counted.json()['metrics']['total']['count'] == expected
    # Separate tenant sees neither counts nor author names.
    async with kb.client('other') as other:
        stats = (await other.get(PREFIX + '/stats')).json()
        assert stats['metrics']['total']['count'] == 0
        assert stats['contributions'] == []
        assert all(p['count'] == p['external_count'] == 0 for p in stats['trend'])


@pytest.mark.asyncio
async def test_dashboard_normal_contributor_and_fixed_catalog_product_order(kb):
    async with kb.client() as client:
        catalog = catalog_values()
        catalog['products'].reverse()
        assert (await client.put(PREFIX + '/catalog', json=catalog, headers=headers())).status_code == 200
        for product in ('general', 'test_product'):
            response = await client.post(PREFIX + '/materials', json=selection(
                name=product, product_code=product,
            ).model_dump(mode='json'), headers=headers())
            assert response.status_code == 201, response.text
        stats = (await client.get(PREFIX + '/stats')).json()
        assert stats['metrics']['mine']['count'] == 2
        assert stats['metrics']['contributors']['count'] == 1
        assert [p['code'] for p in stats['products']] == ['test_product', 'general']
        code = stats['contributions'][0]['code']
        assert code == 'user:' + kb.actors['owner'].user_id
        listing = await client.get(PREFIX + '/materials', params={'contributor_code': code, 'limit': 1})
        assert listing.json()['total'] == 2 and len(listing.json()['items']) == 1

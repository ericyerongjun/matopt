#!/usr/bin/env python3
"""Real-kernel HTTP smoke tests for native UI computation and recovery.

Run with backend/.venv/bin/python scripts/check_ui_math.py. Uses a disposable
database, no model calls, and the same handlers as the browser preview.
"""
import asyncio
import math
from preview_intelligent_ui import get_runtime, seed
from app.main import create_app
from httpx import ASGITransport, AsyncClient


def events(response):
    import json
    assert response.status_code == 200, response.text
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]


async def main():
    rt = get_runtime()
    await rt.start()
    cases = dict((sid, cid) for cid, sid in seed(rt))
    try:
        async with AsyncClient(transport=ASGITransport(app=create_app()),base_url='http://test') as client:
            async def run(sid, values, key):
                cid=cases[sid]; surface=rt.harness.ui.store.get(cid,sid)
                request={'action_id':'recompute','revision':surface['revision'],'values':values,'idempotency_key':key}
                frames=events(await client.post(f'/api/conversations/{cid}/ui/{sid}/actions',json=request))
                assert not [e for e in frames if e['type']=='ui_error'], frames
                result=next(e['surface'] for e in frames if e['type']=='ui_commit')
                replay=events(await client.post(f'/api/conversations/{cid}/ui/{sid}/actions',json=request))
                assert next(e['surface'] for e in replay if e['type']=='ui_commit') == result
                return result

            taylor=await run('taylor',{'degree':5},'math-taylor')
            graph=next(n for n in taylor['nodes'] if n['id']=='graph')['props']['data']
            for index in [0,40,60,100,120]:
                x=graph[0]['x'][index]
                assert math.isclose(graph[0]['y'][index],math.sin(x),abs_tol=1e-12)
                assert math.isclose(graph[1]['y'][index],x-x**3/6+x**5/120,abs_tol=1e-12)
            print('PASS Taylor: plotted points agree with independent polynomial evaluation; idempotent replay')

            first=await run('clt',{'n':40},'math-clt')
            rows=next(n for n in first['nodes'] if n['id']=='moments')['props']['rows']
            assert abs(rows[0][1]-.5) < .005
            assert abs(rows[1][1]-1/480)/(1/480) < .08
            response=await client.post(f"/api/conversations/{cases['clt']}/kernel/restart")
            assert response.status_code==200,response.text
            second=await run('clt',{'n':40},'math-clt-after-restart')
            assert first['nodes']==second['nodes']
            print('PASS CLT: moments agree with theory; seeded result survives kernel restart exactly')

            ridge=await run('ridge',{'alpha':2},'math-ridge')
            rows=next(n for n in ridge['nodes'] if n['id']=='coefficients')['props']['rows']
            assert math.isclose(rows[0][1],11/15,abs_tol=1e-12)
            assert math.isclose(rows[1][1],16/15,abs_tol=1e-12)
            print('PASS ridge: coefficients agree with the closed-form 2x2 inverse')

            data=await run('data',{'group':'A'},'math-data')
            assert 'count 2, mean 2' in data['fallback']
            cid=cases['data']; rt.workspace(cid).resolve('measurements.csv').unlink()
            request={'action_id':'recompute','revision':data['revision'],'values':{'group':'B'},'idempotency_key':'math-missing-file'}
            frames=events(await client.post(f'/api/conversations/{cid}/ui/data/actions',json=request))
            assert any(e['type']=='ui_error' and 'missing' in e['error'].lower() for e in frames)
            assert rt.harness.ui.store.get(cid,'data') == data
            history=await client.get(f'/api/conversations/{cid}')
            assert history.status_code==200
            print('PASS CSV: recomputation reads declared file; missing input preserves last good result')
    finally:
        await rt.stop()


if __name__ == '__main__':
    asyncio.run(main())

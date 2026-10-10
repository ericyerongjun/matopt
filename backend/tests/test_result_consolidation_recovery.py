"""Keep explicitly authored findings through writing, checking and crash recovery."""
import asyncio
from copy import deepcopy
import json

import pytest

from app.agent.harness import RunRequest
from app.agent.presentation import public_content
from app.agent.prompts import FINAL_RESPONSE_PROMPT, VERIFIER_PROMPT
from app.agent.runs import RunManager
from app.agent.session import SessionLog
from app.agent.session.repair import open_turn_closers
from app.agent.tools.result_tool import PublishResultTool
from tests.fake_provider import FakeRegistry
from tests.test_agent_activity import rig, Capture, LiveProvider, report
from tests.test_result_publication import block, publish
from tests.test_runs import replay


class ConsolidatingProvider(LiveProvider):
    """A deterministic writer can only retain findings it actually receives."""
    def __init__(self, script=(), *, gate_check=False):
        super().__init__(script)
        self.publication_inputs = []
        self.check_inputs = []
        self.check_started = asyncio.Event()
        self.release_check = asyncio.Event()
        if not gate_check:
            self.release_check.set()

    async def stream(self, messages, tools, options):
        if messages[0]['content'] == FINAL_RESPONSE_PROMPT:
            payload = json.loads(messages[-1]['content'])
            self.publication_inputs.append(deepcopy(payload))
            findings = payload.get('published_results', [])
            if findings:
                # Deliberately do not infer missing numbers from the request.
                payload['answer'] = '\n\n'.join(item['content'] for item in findings)
                messages = deepcopy(messages)
                messages[-1]['content'] = json.dumps(payload)
        elif messages[0]['content'] == VERIFIER_PROMPT:
            self.check_inputs.append(deepcopy(messages))
            self.check_started.set()
            await self.release_check.wait()
        async for event in super().stream(messages, tools, options):
            yield event


def install(rig, provider):
    rig.harness.tools.register(PublishResultTool())
    rig.provider = provider
    rig.harness.providers = FakeRegistry(provider)


@pytest.mark.asyncio
async def test_final_writer_retains_answer_that_only_existed_in_published_result(rig):
    finding = 'The computed value is **123**.'
    provider = ConsolidatingProvider([publish(finding), {'text': 'Completed the calculation.'}])
    install(rig, provider)
    capture = Capture(rig.harness, RunRequest(rig.cid, 'Calculate the value and explain the answer.')).start()
    try:
        await capture.finish()
        payload = provider.publication_inputs[0]
        assert payload['answer'] == 'Completed the calculation.'
        assert payload['published_results'][0]['content'] == finding
        end = next(e for e in capture.events if e['type'] == 'message_end')
        assert end['content']['parts'][0]['text'] == finding
        assert next(p for p in end['content']['parts'] if p['type'] == 'result_block')['block']['status'] == 'superseded'
        saved = rig.db.get_message(end['message_id'])['content']
        assert public_content(saved) == end['content']
    finally:
        await capture.close()


@pytest.mark.asyncio
async def test_checker_sees_current_findings_even_if_terminal_draft_omits_them(rig):
    finding = 'The computed value is 123.'
    provider = ConsolidatingProvider([publish(finding), {'text': 'Completed the calculation.'}, report('correct')])
    install(rig, provider)
    capture = Capture(rig.harness, RunRequest(rig.cid, 'Calculate and verify the value.', verify=True)).start()
    try:
        await capture.finish()
        assert finding in provider.check_inputs[0][-1]['content']
        assert finding in provider.publication_inputs[0]['published_results'][0]['content']
    finally:
        await capture.close()


@pytest.mark.asyncio
async def test_strict_finding_is_consolidated_privately_then_verified_before_release(rig):
    finding = 'The computed value is **123**.'
    provider = ConsolidatingProvider([publish(finding), {'text': 'Completed the calculation.'}, report('correct')], gate_check=True)
    install(rig, provider)
    capture = Capture(rig.harness, RunRequest(rig.cid, 'Calculate and verify the value.', verify=True, strict_final=True)).start()
    try:
        await asyncio.wait_for(provider.check_started.wait(), 5)
        assert provider.publication_inputs[0]['published_results'][0]['content'] == finding
        assert finding in provider.check_inputs[0][-1]['content']
        assert not any(e['type'] in {'result_block', 'text_delta', 'final_start', 'message_end'} for e in capture.events)
        assert finding not in json.dumps(capture.events)
        provider.release_check.set()
        await capture.finish()
        assert len(provider.publication_inputs) == 1  # no unchecked rewrite after checking
        assert not any(e['type'] == 'result_block' for e in capture.events)
        assert next(e for e in capture.events if e['type'] == 'final_start')['mode'] == 'buffered'
        end = next(e for e in capture.events if e['type'] == 'message_end')
        assert end['content']['parts'][0]['text'] == finding
        assert not any(p['type'] == 'result_block' for p in end['content']['parts'])
        assert end['content']['work']['verification']['status'] == 'correct'
    finally:
        provider.release_check.set()
        await capture.close()


@pytest.mark.asyncio
async def test_strict_corrected_finding_is_reconsolidated_before_second_check(rig):
    wrong, corrected = 'The result is 122.', 'The result is 123.'
    provider = ConsolidatingProvider([publish(wrong), {'text': 'Completed.'}, report('incorrect'),
                                     publish(corrected), {'text': 'Corrected.'}, report('correct')])
    install(rig, provider)
    capture = Capture(rig.harness, RunRequest(rig.cid, 'Calculate and verify the value.', verify=True, strict_final=True)).start()
    try:
        await capture.finish()
        assert len(provider.publication_inputs) == 2
        assert provider.publication_inputs[-1]['published_results'][0]['content'] == corrected
        assert wrong not in json.dumps(provider.publication_inputs[-1])
        assert corrected in provider.check_inputs[-1][-1]['content']
        assert wrong not in provider.check_inputs[-1][-1]['content']
        end = next(e for e in capture.events if e['type'] == 'message_end')
        assert end['content']['parts'][0]['text'] == corrected
        assert not any(e['type'] == 'result_block' for e in capture.events)
    finally:
        await capture.close()


@pytest.mark.asyncio
async def test_withdrawn_findings_are_absent_from_final_writer_and_checker(rig):
    provider = ConsolidatingProvider([publish('WITHDRAWN_FINDING'), publish('', status='withdrawn'),
                                     {'text': 'The valid result is 123.'}, report('correct')])
    install(rig, provider)
    capture = Capture(rig.harness, RunRequest(rig.cid, 'Calculate and verify the value.', verify=True)).start()
    try:
        await capture.finish()
        assert provider.publication_inputs[0]['published_results'] == []
        assert 'WITHDRAWN_FINDING' not in json.dumps(provider.publication_inputs)
        assert 'WITHDRAWN_FINDING' not in json.dumps(provider.check_inputs)
    finally:
        await capture.close()


async def private_partial_checkpoint(rig, values, *, strict=False):
    rig.db.create_run('r_private_partial', rig.cid, 'hash')
    rig.db.append_run_event('r_private_partial', 'message_start', {'message_id': 'm_result'}, message_id='m_result', status='running')
    log = await SessionLog.open(rig.db, rig.cid)
    log.set_system({'role': 'system', 'content': 'test'})
    log.append('turn/start', {'turn': 1, 'message_id': 'm_result', 'presentation': 'final'})
    log.append('step/start', {'turn': 1, 'step': 1})
    log.append('assistant/message', {'turn': 1, 'step': 1, 'stream': [],
        'message': {'role': 'assistant', 'content': 'PRIVATE_WORKER_CANARY'}}, surface_op='append')
    log.append('step/end', {'turn': 1, 'step': 1})
    for value in values:
        log.append('ui/result', {'turn': 1, 'step': 1, 'message_id': 'm_result',
                                'block': value, 'strict_final': strict}, ignorable=True)
    await log.flush()
    # Intentionally never publish a corresponding public result_block frame.
    return log


@pytest.mark.asyncio
async def test_restart_recovers_latest_private_receipt_when_public_stream_never_saw_it(rig):
    values = [block(content='Earlier'), block(revision=3, content='Latest useful finding'), block(revision=2, content='Stale')]
    await private_partial_checkpoint(rig, values)
    manager = RunManager(rig.db, rig.harness)
    try:
        await manager.recover()
        events = await replay(manager, 'r_private_partial')
        end = next(e for e in events if e['type'] == 'message_end')
        assert end['content']['parts'] == [{'type': 'result_block', 'block': values[1]}]
        assert 'PRIVATE_WORKER_CANARY' not in json.dumps(events)
        assert not rig.provider.requests
        assert public_content(rig.db.get_message('m_result')['content']) == end['content']
        await manager.recover()
        assert await replay(manager, 'r_private_partial') == events
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_private_withdrawal_overrides_older_public_journal_revision(rig):
    await private_partial_checkpoint(rig, [block(), block(revision=3, status='withdrawn', content='REMOVED_CANARY')])
    rig.db.append_run_event('r_private_partial', 'result_block', {'message_id': 'm_result', 'block': block(revision=2)})
    manager = RunManager(rig.db, rig.harness)
    try:
        await manager.recover()
        events = await replay(manager, 'r_private_partial')
        end = next(e for e in events if e['type'] == 'message_end')
        recovered = end['content']['parts'][0]['block']
        assert recovered['revision'] == 3 and recovered['status'] == 'withdrawn' and recovered['content'] == ''
        assert 'REMOVED_CANARY' not in json.dumps(end)
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_strict_private_result_never_exposed_by_restart(rig):
    await private_partial_checkpoint(rig, [block(content='STRICT_FINDING_CANARY')], strict=True)
    manager = RunManager(rig.db, rig.harness)
    try:
        await manager.recover()
        events = await replay(manager, 'r_private_partial')
        assert 'STRICT_FINDING_CANARY' not in json.dumps(events)
        assert 'PRIVATE_WORKER_CANARY' not in json.dumps(events)
        content = rig.db.get_message('m_result')['content']
        assert content['strict_final'] is True and public_content(content)['parts'] == []
    finally:
        await manager.shutdown()


def test_recovery_ignores_other_message_receipts_and_strips_unknown_fields():
    log = SessionLog()
    log.append('turn/start', {'turn': 1, 'message_id': 'm_result', 'presentation': 'final'})
    log.append('ui/result', {'message_id': 'm_other', 'block': block(content='OTHER_CANARY')}, ignorable=True)
    log.append('ui/result', {'message_id': 'm_result', 'block': block(reasoning='PRIVATE_CANARY')}, ignorable=True)
    recovered = next(event['data']['content'] for event in open_turn_closers(log.events) if event['type'] == 'ui/message')
    assert public_content(recovered)['parts'] == [{'type': 'result_block', 'block': block()}]
    assert 'CANARY' not in json.dumps(public_content(recovered))

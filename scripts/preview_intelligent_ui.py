#!/usr/bin/env python3
"""Disposable UI preview with scripted provider and real Python computations.

backend/.venv/bin/python scripts/preview_intelligent_ui.py --port 8001
MATOPT_API_ORIGIN=http://127.0.0.1:8001 npm --prefix frontend run dev

No API keys, existing conversations, or production data are used.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.update(DATA_DIR=tempfile.mkdtemp(prefix='matopt-ui-preview-'), MCP_ENABLED='false', DEEPSEEK_API_KEY='scripted-preview-only', SESSION_LOG_ENABLED='true')

from app.llm.base import LLMProvider, StreamDelta, ToolCallDelta
from app.llm.registry import providers
from app.runtime import get_runtime
from ui_examples import seed, TAYLOR

class PreviewProvider(LLMProvider):
    id = 'deepseek'
    async def stream(self, messages, tools, options):
        if messages[-1]['role'] == 'tool':
            yield StreamDelta(kind='text', text='This result is saved with the chat. Open it in canvas for more space, or export its data.'); yield StreamDelta(kind='finish',finish_reason='stop'); return
        scope = {}; exec(TAYLOR, scope)
        payload = {'id':'taylor-live','title':'Taylor approximation','nodes':scope['compute']({'degree':3})['nodes'][2:], 'fallback':scope['compute']({'degree':3})['fallback']}
        yield StreamDelta(kind='text',text='Here is a computed approximation to explore. ')
        yield StreamDelta(kind='tool_call',tool_call=ToolCallDelta(index=0,id='preview-ui',name='render_ui',arguments=''))
        raw = json.dumps(payload)
        for i in range(0,len(raw),100):
            yield StreamDelta(kind='tool_call',tool_call=ToolCallDelta(index=0,arguments=raw[i:i+100])); await asyncio.sleep(.01)
        yield StreamDelta(kind='finish',finish_reason='tool_calls')
    async def complete_text(self,messages,options):
        return '["Why does the approximation improve near zero?", "What changes at higher degrees?"]'

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--port',type=int,default=8001); args=parser.parse_args()
    providers._instances['deepseek']=PreviewProvider()
    rt=get_runtime(); seed(rt)
    import uvicorn
    from app.main import create_app
    uvicorn.run(create_app(),host='127.0.0.1',port=args.port)

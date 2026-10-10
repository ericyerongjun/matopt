"""Deterministic examples for intelligent UI smoke tests and local previews."""
import asyncio
import json
import math
import textwrap


TAYLOR = textwrap.dedent('''
def compute(values):
    import math
    degree = int(values['degree'])
    x = [i / 20 for i in range(-60, 61)]
    approx = [sum((-1)**k * t**(2*k+1) / math.factorial(2*k+1) for k in range((degree+1)//2)) for t in x]
    exact = [math.sin(t) for t in x]
    error = max(abs(a-b) for a, b in zip(approx, exact))
    return {'state': {'degree': degree}, 'fallback': f'Degree {degree} Taylor approximation to sin(x), centered at zero. Maximum sampled absolute error on [-3, 3]: {error:.6g}. x is in radians.', 'nodes': [
        {'id':'controls', 'type':'slider', 'props':{'label':'Polynomial degree', 'stateKey':'degree', 'min':1, 'max':15, 'step':2}},
        {'id':'compute', 'type':'button', 'props':{'label':'Recompute approximation', 'actionId':'recompute'}},
        {'id':'graph', 'type':'plot', 'props':{'label':'sin(x) and its Taylor approximation', 'caption':'Radians · expansion about x = 0 · 121 sampled points', 'data':[
            {'type':'scatter', 'mode':'lines', 'name':'sin(x)', 'x':x, 'y':exact},
            {'type':'scatter', 'mode':'lines', 'name':f'Degree {degree}', 'x':x, 'y':approx, 'line':{'dash':'dash'}}], 'layout':{'height':320, 'margin':{'l':44,'r':16,'t':52,'b':44},'legend':{'orientation':'h','x':0,'y':1.18},'xaxis':{'title':{'text':'x (radians)'}},'yaxis':{'title':{'text':'f(x)'}}}}},
        {'id':'error','type':'comparison','props':{'items':[{'label':'Polynomial degree','value':degree},{'label':'Maximum sampled error','value':round(error,6)}]}}
    ]}
''')

CLT = textwrap.dedent('''
def compute(values):
    import numpy as np
    n = int(values['n'])
    means = np.random.default_rng(7).uniform(size=(4000,n)).mean(axis=1)
    mean, variance = float(means.mean()), float(means.var(ddof=1))
    return {'state':{'n':n},'fallback':f'4,000 uniform [0,1] sample means, n={n}, seed=7. Mean {mean:.6f}; variance {variance:.6f}; theoretical variance {1/(12*n):.6f}.', 'nodes':[
        {'id':'n','type':'number','props':{'label':'Sample size','stateKey':'n','min':1,'max':1000,'step':1}},
        {'id':'compute','type':'button','props':{'label':'Run seeded simulation','actionId':'recompute'}},
        {'id':'distribution','type':'plot','props':{'data':[{'type':'histogram','x':means.tolist(),'nbinsx':35}],'layout':{'height':320,'xaxis':{'title':{'text':'Sample mean'}},'yaxis':{'title':{'text':'Count'}}}}},
        {'id':'moments','type':'table','props':{'columns':['quantity','observed','theoretical'],'rows':[['mean',mean,0.5],['variance',variance,1/(12*n)]]}}
    ]}
''')

RIDGE = textwrap.dedent(r'''
def compute(values):
    import numpy as np
    alpha = float(values['alpha'])
    X, y = np.array([[1.,0.],[0.,1.],[1.,1.]]), np.array([1.,2.,3.])
    beta = np.linalg.solve(X.T @ X + alpha*np.eye(2), X.T @ y)
    return {'state':{'alpha':alpha},'fallback':f'Ridge solution, no intercept, objective ||Xb-y||² + alpha||b||². alpha={alpha:g}; coefficients {beta.tolist()}.', 'nodes':[
        {'id':'alpha','type':'slider','props':{'label':'Regularization alpha','stateKey':'alpha','min':0,'max':10,'step':0.25}},
        {'id':'compute','type':'button','props':{'label':'Fit ridge model','actionId':'recompute'}},
        {'id':'formula','type':'equation','props':{'latex':r'\hat{\beta}=(X^\top X+\alpha I)^{-1}X^\top y'}},
        {'id':'coefficients','type':'table','props':{'columns':['coefficient','value'],'rows':[['beta 1',float(beta[0])],['beta 2',float(beta[1])]]}}
    ]}
''')

CSV = textwrap.dedent('''
def compute(values):
    import csv
    with open('measurements.csv', newline='') as file:
        rows = list(csv.DictReader(file))
    group = values['group']
    selected = [r for r in rows if group == 'all' or r['group'] == group]
    mean = sum(float(r['value']) for r in selected)/len(selected)
    return {'state':{'group':group},'fallback':f'measurements.csv: group {group}, count {len(selected)}, mean {mean:g}. Synthetic demonstration data.', 'nodes':[
        {'id':'group','type':'select','props':{'label':'Group','stateKey':'group','options':['all','A','B']}},
        {'id':'compute','type':'button','props':{'label':'Recompute file summary','actionId':'recompute'}},
        {'id':'summary','type':'comparison','props':{'items':[{'label':'Rows','value':len(selected)},{'label':'Mean','value':mean}]}},
        {'id':'rows','type':'table','props':{'columns':['group','value'],'rows':selected}}
    ]}
''')

MINI_APP = '''<!doctype html><html><head><style>
body{font:16px/1.5 system-ui;margin:0;padding:20px}h3{font-size:18px;font-weight:500;margin:0 0 8px}p{color:gray}label{display:flex;justify-content:space-between}input{width:100%}canvas{width:100%;height:200px}small{color:gray}
</style></head><body><h3>Explore a sine wave</h3><p>Change the frequency to see the wave respond immediately.</p>
<label for="frequency">Frequency <output id="value"></output></label><input id="frequency" type="range" min="1" max="8" step="1" value="2"><canvas width="800" height="240" aria-label="Sine wave graph"></canvas><small>x in radians · amplitude 1 · computed locally</small>
<script>
const input=document.getElementById('frequency'), output=document.getElementById('value'), canvas=document.querySelector('canvas'), ctx=canvas.getContext('2d');
function draw(state){input.value=state.frequency;output.textContent=state.frequency;ctx.clearRect(0,0,800,240);ctx.strokeStyle=matopt.getTheme()==='dark'?'#ddd':'#222';ctx.lineWidth=2;ctx.beginPath();for(let i=0;i<800;i++){const y=120-90*Math.sin(i/800*2*Math.PI*state.frequency);i?ctx.lineTo(i,y):ctx.moveTo(i,y);}ctx.stroke();}
input.addEventListener('input',()=>matopt.setState({frequency:Number(input.value)}));matopt.onStateChange(draw);matopt.onThemeChange(()=>draw(matopt.getState()));draw(matopt.getState());
</script></body></html>'''

EXAMPLES = [
    ('taylor', 'Taylor approximation', TAYLOR, {'degree':3}, {'degree':{'type':'integer','minimum':1,'maximum':15}}, []),
    ('clt', 'Central limit theorem', CLT, {'n':30}, {'n':{'type':'integer','minimum':1,'maximum':1000}}, []),
    ('ridge', 'Ridge regression', RIDGE, {'alpha':1}, {'alpha':{'type':'number','minimum':0,'maximum':10}}, []),
    ('data', 'Uploaded data summary', CSV, {'group':'all'}, {'group':{'type':'string','enum':['all','A','B']}}, ['measurements.csv']),
]

def seed(rt):
    from app.agent.tools.base import ToolContext
    conversations = []
    for sid, title, code, initial, properties, files in EXAMPLES:
        cid = rt.db.create_conversation(title=title, provider='deepseek', model='deepseek-flash', reasoning='high')['id']
        workspace = rt.workspace(cid)
        workspace.resolve('measurements.csv').write_text('group,value\nA,1\nA,3\nB,8\nB,12\n')
        ctx = ToolContext(cid, workspace, lambda _: None, asyncio.Event(), kernels=rt.kernels, settings=rt.harness.settings)
        manifest = rt.harness.ui.register(ctx, sid, 'recompute', 'Recompute', {'type':'object','properties':properties,'required':list(properties)}, code, input_files=files, seed=7)
        if sid == 'taylor':
            scope = {}; exec(code, scope); change = scope['compute'](initial)
        else:
            change = {'nodes':[{'id':'intro','type':'markdown','props':{'text':'Choose parameters, then recompute this reproducible example.'}}], 'state':initial,'fallback':title+' — ready to compute.'}
        surface = rt.harness.ui.commit(ctx, {'id':sid,'title':title,'actions':[manifest],**change})
        rt.db.add_message(cid, 'user', {'text':f'Help me explore {title.lower()} interactively.', 'attachments':[]})
        rt.db.add_message(cid, 'assistant', {'parts':[{'type':'text','text':'Adjust the parameters and inspect the result. Computation happens only when you request it.'},{'type':'ui','surface':surface}], 'provider':'deepseek','model':'deepseek-flash','reasoning':'high','usage':{},'status':'complete','plan':[],'stats':{'llm_calls':0,'tool_calls':0}})
        conversations.append((cid, sid))
    cid = rt.db.create_conversation(title='Interactive sine wave',provider='deepseek',model='deepseek-flash')['id']
    ctx = ToolContext(cid, rt.workspace(cid), lambda _: None, asyncio.Event(), kernels=rt.kernels, settings=rt.harness.settings)
    app = rt.harness.ui.commit(ctx, {'id':'sine-wave','kind':'mini_app','title':'Sine wave explorer','fallback':'A sine wave with amplitude 1 and adjustable frequency. x is in radians.','app':{'html':MINI_APP},'state':{'frequency':2}})
    rt.db.add_message(cid,'assistant',{'parts':[{'type':'ui','surface':app}], 'provider':'deepseek','model':'deepseek-flash','reasoning':'high','usage':{},'status':'complete','plan':[],'stats':{'llm_calls':0,'tool_calls':0}})
    seed_rendering(rt)
    return conversations


def seed_rendering(rt):
    """Synthetic source/rendered examples; never edits a user's saved chat."""
    html = '''<!doctype html><html><head><style>body{font:16px/1.6 system-ui;margin:0;padding:24px}h2{font-size:20px;font-weight:500;margin:0 0 16px}button{border:1px solid #aaa;border-radius:999px;padding:8px 16px;background:transparent;color:inherit;font:inherit;cursor:pointer}output{margin-left:12px;font-variant-numeric:tabular-nums}</style></head><body><h2>Sampling experiment</h2><p>A self-contained HTML result.</p><button onclick="document.querySelector('output').textContent=Number(document.querySelector('output').textContent)+1">Add a sample</button><output>0</output></body></html>'''
    svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 180" role="img" aria-label="A simple computation flow"><rect width="640" height="180" rx="16" fill="#f7f7f7"/><g fill="none" stroke="#333" stroke-width="2"><rect x="30" y="60" width="150" height="60" rx="12"/><path d="M180 90h55m-8-7 8 7-8 7"/><rect x="245" y="60" width="150" height="60" rx="12"/><path d="M395 90h55m-8-7 8 7-8 7"/><rect x="460" y="60" width="150" height="60" rx="12"/></g><g fill="#171717" font-family="system-ui" font-size="18" text-anchor="middle"><text x="105" y="97">Input</text><text x="320" y="97">Compute</text><text x="535" y="97">Result</text></g></svg>'''
    markdown = r'''## A rendered answer

For $x^2-5x+6=0$, the roots are $x=2$ and $x=3$.

$$\int_0^1 x^2\,dx=\boxed{\frac{1}{3}}$$

The numeric indices $1,2,3,7,8,9,10$ are mathematics. The price $1,000 is ordinary currency.

| Value | Square | Result |
| :--- | ---: | :---: |
| 2 | 4 | ✓ |
| 3 | 9 | ✓ |

The following template is requested as **literal Markdown source**:

```markdown
## Copyable source
**Bold** and $x^2$ stay literal inside this example.
```

## HTML output

```html-preview
''' + html + '''
```

## SVG output

```svg-preview
''' + svg + '''
```

## HTML source example

```html
<button>Example source</button>
```
'''
    cid = rt.db.create_conversation(title='Rich output examples', provider='deepseek', model='deepseek-flash')['id']
    workspace = rt.workspace(cid)
    for name, text in [('answer.md', markdown), ('experiment.html', html), ('flow.svg', svg)]:
        workspace.resolve(name).write_text(text)
    rt.db.add_message(cid, 'user', {'text':'/python Show a table, mathematics and visual HTML/SVG output. Preserve the source examples.', 'attachments':[]})
    rt.db.add_message(cid, 'assistant', {'parts':[{'type':'text','text':markdown}], 'presentation':'final', 'provider':'deepseek','model':'deepseek-flash','reasoning':'high','usage':{},'status':'complete','plan':[],'stats':{'llm_calls':0,'tool_calls':0}})

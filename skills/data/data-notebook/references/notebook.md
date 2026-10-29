# Build an analysis notebook with nbformat

```python
import nbformat as nbf
from pathlib import Path

nb = nbf.v4.new_notebook()
nb["metadata"] = {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}}
cells = [
    nbf.v4.new_markdown_cell("# Title\n\nOne paragraph: the question and the table."),
    nbf.v4.new_code_cell(
        "from pathlib import Path\nimport pandas as pd\n"
        "p = next(Path('uploads').glob('*.csv'))\n"
        "df = pd.read_csv(p)\n"
        "print(p.name, df.shape)\n"
        "df.head()"
    ),
    nbf.v4.new_markdown_cell("## Findings\nReplace with the actual conclusions."),
]
nb["cells"] = cells
Path("analysis.ipynb").write_text(nbf.writes(nb), encoding="utf-8")
print("wrote analysis.ipynb")
```

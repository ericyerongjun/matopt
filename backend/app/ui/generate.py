"""Generate public JSON Schema; run `python -m app.ui.generate` in backend."""
import json
from pathlib import Path
from .contracts import UISurface, UIStateRequest, UIActionRequest


def main():
    schema = UISurface.model_json_schema()
    schema["$id"] = "https://matopt.local/schemas/ui/v1/surface.json"
    target = Path(__file__).with_name("contract.schema.json")
    target.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n")
    for name, model in (("state-request", UIStateRequest), ("action-request", UIActionRequest)):
        target.with_name(name + ".schema.json").write_text(json.dumps(model.model_json_schema(), indent=2) + "\n")

if __name__ == "__main__":
    main()

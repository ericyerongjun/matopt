"""Long-term memory across conversations, ported from Codex ``memories`` / ``ext/memories``.

Phase 1 (:mod:`.extract`) turns each idle conversation into a small record;
Phase 2 (:mod:`.consolidate`) folds the records and the user's ad-hoc notes into
``memory_summary.md`` (injected into every system prompt) and ``MEMORY.md``
(read on demand through :mod:`.tools`). :mod:`.service` runs both phases and
serves the read path; :mod:`.store` owns the files under ``<data_dir>/memories``.
Everything is off unless ``settings.memories_enabled``.
"""

from app.agent.memory.service import MemoryService, get_memory_service, set_memory_service
from app.agent.memory.store import MemoryPathError, MemoryStore

__all__ = ["MemoryService", "MemoryStore", "MemoryPathError", "get_memory_service", "set_memory_service"]

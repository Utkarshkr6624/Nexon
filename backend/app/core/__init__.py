"""Cross-cutting application infrastructure.

Everything in this package is HTTP-agnostic (except ``middleware`` and ``deps``,
which exist precisely to wire HTTP into the application). Modules import from
the specific submodule (``from app.core.config import get_settings``) rather
than from here, so that this package stays free of import cycles.
"""

__all__: list[str] = []

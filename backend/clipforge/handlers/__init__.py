"""Importing this package registers every job handler with the worker."""

import importlib
import importlib.util

MODULES = ("media_jobs", "video_workflow", "publish_jobs", "analytics_jobs", "maintenance_jobs", "live_jobs")

for _name in MODULES:
    _full = f"{__name__}.{_name}"
    if importlib.util.find_spec(_full) is not None:
        importlib.import_module(_full)

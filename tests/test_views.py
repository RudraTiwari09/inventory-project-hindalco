"""
Unit tests for Milestone 6: View modules and navigation.
"""

import pytest
from app.views.overview import render as render_overview
from app.views.inventory import render as render_inventory
from app.views.ptw import render as render_ptw
from app.views.monthly_changes import render_monthly_changes
from app.views.upload_history import render_upload_history


def test_view_modules_import():
    # Verify all view modules compile and import cleanly
    import app.views.overview
    import app.views.inventory
    import app.views.ptw
    import app.views.reconciliation
    import app.views.upload
    import app.views.monthly_changes
    import app.views.upload_history
    import app.views.login
    import app.views.landing
    assert True


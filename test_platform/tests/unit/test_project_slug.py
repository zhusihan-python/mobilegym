from test_platform.domain.projects import make_project_slug, next_available_slug


def test_project_slug_is_deterministic_ascii_token():
    assert make_project_slug("Mobile App Regression") == "mobile-app-regression"
    assert make_project_slug("  Mobile   App Regression!  ") == "mobile-app-regression"
    assert make_project_slug("!!!") == "project"


def test_next_available_slug_appends_numeric_suffix_for_collisions():
    existing = {"mobile-app-regression", "mobile-app-regression-2"}

    assert next_available_slug("Mobile App Regression", existing) == "mobile-app-regression-3"

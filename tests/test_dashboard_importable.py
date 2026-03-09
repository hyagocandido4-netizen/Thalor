def test_dashboard_modules_importable():
    # Dashboard must be importable without Streamlit installed.
    import natbin.dashboard  # noqa: F401
    import natbin.dashboard.app  # noqa: F401
    import natbin.dashboard.__main__  # noqa: F401

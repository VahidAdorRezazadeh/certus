"""Console entry points installed by pip: certus, certus-gui, certus-cad."""
import os
import sys


def gui():
    from streamlit.web import cli as stcli
    app = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")
    # white background, same as .streamlit/config.toml; given here so the
    # theme applies whatever folder certus-gui is started from
    theme = ["--theme.base=light",
             "--theme.backgroundColor=#FFFFFF",
             "--theme.secondaryBackgroundColor=#F5F6F8",
             "--theme.textColor=#1A1A1A"]
    sys.argv = ["streamlit", "run", app] + theme + sys.argv[1:]
    sys.exit(stcli.main())


def model():
    from certus.model_agent import main
    sys.exit(main())

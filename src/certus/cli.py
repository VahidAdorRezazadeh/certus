"""Console entry points installed by pip: certus, certus-gui, certus-cad."""
import os
import sys


def gui():
    from streamlit.web import cli as stcli
    app = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")
    sys.argv = ["streamlit", "run", app] + sys.argv[1:]
    sys.exit(stcli.main())


def model():
    from certus.model_agent import main
    sys.exit(main())

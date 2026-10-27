"""Entry point on Hugging Face Spaces.

Free Spaces now run on the Gradio SDK with ZeroGPU hardware, which expects Gradio to start the server and
at least one @spaces.GPU function to exist. The demo itself is the FastAPI site in app/server.py and needs
no GPU: all model calls go to Meta Model API. So Gradio starts the server, and the site's routes are put in
front of Gradio's own; the Gradio page stays reachable at /status.
"""

import gradio as gr
import spaces

from app.server import app as site


@spaces.GPU
def _unused() -> None:
    """Never called. Present only because ZeroGPU Spaces refuse to start without a GPU function."""


with gr.Blocks(title='Anyone Hear Me: status') as status:
    gr.Markdown('Anyone Hear Me is running. Open the [demo](/).')

if __name__ == '__main__':
    status.launch(server_name='0.0.0.0', server_port=7860, prevent_thread_lock=True, ssr_mode=False)
    server = status.app
    server.router.routes[0:0] = list(site.router.routes)
    status.block_thread()

"""Entry point on Hugging Face Spaces.

Free Spaces now run on the Gradio SDK with ZeroGPU hardware, which expects a Gradio app and at least one
@spaces.GPU function. The demo itself is the FastAPI site in app/server.py and needs no GPU: all model
calls go to Meta Model API. So this file serves that site and mounts a one-line Gradio page at /status.
"""

import gradio as gr
import spaces
import uvicorn

from app.server import app as site


@spaces.GPU
def _unused() -> None:
    """Never called. Present only because ZeroGPU Spaces refuse to start without a GPU function."""


with gr.Blocks(title='Anyone Hear Me: status') as status:
    gr.Markdown('Anyone Hear Me is running. Open the [demo](/).')

app = gr.mount_gradio_app(site, status, path='/status')

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=7860)

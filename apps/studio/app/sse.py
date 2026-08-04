from fastapi.responses import StreamingResponse

def sse_from_generator(gen):
    def event_stream():
        for line in gen:
            yield f"data: {line}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(event_stream(), media_type="text/event-stream")

def locked_generator(gen):
    """Hold OPERATION_LOCK for the full lifetime of streaming `gen`."""
    from app.locks import OPERATION_LOCK
    with OPERATION_LOCK:
        yield from gen

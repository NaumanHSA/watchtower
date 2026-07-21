import cv2
from fastapi import FastAPI, Response
from fastapi.responses import StreamingResponse
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic()
app = FastAPI(title="Webcam Stream")
camera = cv2.VideoCapture(0)  # 0 = default webcam

def verify(credentials: HTTPBasicCredentials = Depends(security)):
    if not (credentials.username == "user" and credentials.password == "pass"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return credentials.username

def generate_frames():
    while True:
        success, frame = camera.read()
        if not success:
            break
        # Encode frame as JPEG
        ret, buffer = cv2.imencode('.jpg', frame)
        if not ret:
            continue
        frame_bytes = buffer.tobytes()
        # Yield as MJPEG stream
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
        )

@app.get("/video_feed")
# async def video_feed(user: str = Depends(verify)):
async def video_feed():
    return StreamingResponse(
        generate_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9999)

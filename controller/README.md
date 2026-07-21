# VideoFlow Controller: Distributed Video Processing Orchestrator

![GitHub](https://img.shields.io/badge/license-MIT-blue.svg)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115.0-009688.svg?logo=fastapi)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-2CA5E0?style=flat&logo=docker&logoColor=white)](https://www.docker.com/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

## 🌟 Overview

VideoFlow Controller is a high-performance, distributed video processing orchestration service built with FastAPI. It serves as the central brain for managing a fleet of video processing workers, handling task distribution, health monitoring, and automatic recovery.

### 🔍 Key Features

- **Distributed Worker Management**: Register, monitor, and manage multiple video processing workers
- **Automatic Failover**: Self-healing system that recovers failed tasks and reassigns them to healthy workers
- **Real-time Health Monitoring**: Continuous health checks and automatic removal of unresponsive workers
- **RESTful API**: Intuitive HTTP endpoints for all operations with OpenAPI documentation
- **Scalable Architecture**: Built to handle high-throughput video processing workloads
- **Real-time Notifications**: WebSocket-based event system for real-time updates
- **Detailed Logging**: Comprehensive logging with structured JSON output for easy analysis
- **Container Ready**: Docker and Docker Compose support for easy deployment

## 🚀 Getting Started

### Prerequisites

- Python 3.11 or higher
- MongoDB (for persistent storage)
- Redis (optional, for cross-instance WebSocket notifications)
- Docker and Docker Compose (for containerized deployment)

### Installation

#### Method 1: Local Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/video-worker-controller.git
   cd video-worker-controller
   ```

2. **Create and activate a virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: .\venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**
   Create a `.env` file in the project root with the following variables:
   ```env
   # MongoDB Configuration
   MONGO_URI=mongodb://localhost:27017
   MONGO_DB=video_controller
   
   # API Keys (generate secure random strings)
   WORKER_API_KEY=your_worker_api_key_here
   
   # Application Settings
   APP_NAME=VideoFlow-Controller
   APP_VERSION=1.0.0
   HOST=0.0.0.0
   PORT=7000
   LOG_LEVEL=INFO
   
   # Recovery and Watchdog Settings
   RECOVERY_INTERVAL=20
   WATCHDOG_INTERVAL=20
   
   # Networking
   REQUEST_TIMEOUT_SECONDS=10
   REQUEST_MAX_RETRIES=2
   
   # CORS (comma-separated list of allowed origins, or * for all)
   CORS_ALLOW_ORIGINS=*
   
   # WebSocket Settings
   WS_PING_INTERVAL_SECONDS=20
   WS_SEND_QUEUE_SIZE=100
   
   # Redis URL (optional, required for multi-instance WebSocket support)
   # REDIS_URL=redis://localhost:6379/0
   
   ```

#### Method 2: Docker Deployment

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/video-worker-controller.git
   cd video-worker-controller
   ```

2. **Create a `.env` file** as described above

3. **Build and start the services**
   ```bash
   docker-compose up -d
   ```

   This will start:
   - The VideoFlow Controller on port 7000
   - MongoDB on port 27017
   - Redis on port 6379 (optional)

## 🛠 Configuration Reference

### Application Configuration

| Environment Variable      | Description | Required | Default |
|---------------------------|-------------|----------|---------|
| `APP_NAME` | Name of the application | No | `VideoFlow-Controller` |
| `APP_VERSION` | Application version | No | `1.0.0` |
| `HOST` | Host to bind the application to | No | `0.0.0.0` |
| `PORT` | Port to run the application on | No | `7000` |
| `LOG_LEVEL` | Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL) | No | `INFO` |
| `WORKER_API_KEY` | API key for worker authentication | Yes | - |

### Database Configuration

| Environment Variable | Description | Required | Default |
|----------------------|-------------|----------|---------|
| `MONGO_URI` | MongoDB connection string | Yes | - |
| `MONGO_DB` | MongoDB database name | No | `controller` |
| `REDIS_URL` | Redis connection URL (for WebSocket pub/sub) | No | - |

### Performance Tuning

| Environment Variable | Description | Default |
|----------------------|-------------|---------|
| `RECOVERY_INTERVAL` | Interval (in seconds) between recovery job runs | `20` |
| `WATCHDOG_INTERVAL` | Interval (in seconds) between worker health checks | `20` |
| `REQUEST_TIMEOUT_SECONDS` | HTTP request timeout in seconds | `10` |
| `REQUEST_MAX_RETRIES` | Maximum number of retries for failed requests | `2` |
| `WS_PING_INTERVAL_SECONDS` | WebSocket ping interval in seconds | `20` |
| `WS_SEND_QUEUE_SIZE` | Maximum WebSocket send queue size | `100` |

## 🌐 API Documentation

### Base URL
All API endpoints are prefixed with `/api/v1`.

### Authentication
- **Worker API Key**: Required for worker-specific endpoints
  - Header: `X-API-Key: your_worker_api_key`

### Health Check

#### GET /health
Check if the service is running.

**Response**:
```json
{
  "status": "ok"
}
```

### Worker Management

#### POST /api/v1/internal/worker/go_live
Register a new worker.

**Request Body**:
```json
{
  "worker_id": "worker-1",
  "worker_url": "http://worker-1:8000",
  "controller_url": "http://controller:7000",
  "capabilities": {
    "max_allowed_streams": 10,
    "supported_codecs": ["h264", "h265"]
  },
  "host_info": {
    "cpu_cores": 8,
    "gpu_available": true,
    "memory_gb": 32
  }
}
```

**Response**:
```json
{
  "ok": true,
  "message": "Worker registered successfully"
}
```

#### PATCH /api/v1/internal/worker/heartbeat/{worker_id}
Update worker heartbeat and stream status.

**Request Body**:
```json
{
  "status": "online",
  "streams": [
    {
      "id": "stream-123",
      "status": "running"
    }
  ]
}
```

**Response**:
```json
{
  "ok": true,
  "message": "Heartbeat processed",
  "details": {
    "missing_streams": []
  }
}
```

#### GET /api/v1/dashboard/workers
List all workers.

**Query Parameters**:
- `status`: Filter workers by status (e.g., `online`, `offline`)

**Response**:
```json
[
  {
    "worker_id": "worker-1",
    "worker_url": "http://worker-1:8000",
    "status": "online",
    "last_seen": "2023-10-07T12:00:00Z",
    "assigned_stream_count": 3,
    "capabilities": {
      "max_allowed_streams": 10,
      "supported_codecs": ["h264", "h265"]
    },
    "host_info": {
      "cpu_cores": 8,
      "gpu_available": true,
      "memory_gb": 32
    }
  }
]
```

### Stream Management

#### POST /api/v1/dashboard/stream/assign
Assign a new stream to a worker.

**Request Body**:
```json
{
  "source_url": "rtsp://camera.example.com/stream",
  "stream_name": "Front Door Camera",
  "stream_location": "front_door",
  "worker_id": "worker-1",
  "metadata": {
    "camera_id": "cam-123",
    "location": "Front Entrance"
  }
}
```

**Response**:
```json
{
  "stream_id": "stream-123",
  "worker_id": "worker-1",
  "status": "assigned",
  "webrtc_url": "wss://example.com/stream/stream-123/webrtc"
}
```

#### GET /api/v1/dashboard/stream
List all streams.

**Query Parameters**:
- `worker_id`: Filter streams by worker ID
- `status`: Filter streams by status

**Response**:
```json
[
  {
    "stream_id": "stream-123",
    "stream_name": "Front Door Camera",
    "stream_location": "front_door",
    "source_url": "rtsp://camera.example.com/stream",
    "worker_id": "worker-1",
    "status": "running",
    "webrtc_url": "wss://example.com/stream/stream-123/webrtc",
    "metadata": {
      "camera_id": "cam-123",
      "location": "Front Entrance"
    },
    "created_at": "2023-10-07T12:00:00Z",
    "updated_at": "2023-10-07T12:05:00Z"
  }
]
```

#### POST /api/v1/callbacks/stream/{stream_id}/status
Update stream status (called by workers).

**Request Body**:
```json
{
  "status": "running",
  "webrtc_url": "wss://example.com/stream/stream-123/webrtc",
  "metadata": {
    "bitrate": 2000000,
    "resolution": "1920x1080"
  }
}
```

**Response**:
```json
{
  "ok": true
}
```

## 🐳 Docker Deployment

### Prerequisites
- Docker 20.10.0+
- Docker Compose 2.0.0+

### Quick Start

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/video-worker-controller.git
   cd video-worker-controller
   ```

2. **Configure environment variables**
   Copy the example environment file and update it with your settings:
   ```bash
   cp .env.example .env
   nano .env  # Edit the file with your configuration
   ```

3. **Build and start the services**
   ```bash
   docker-compose up -d
   ```

4. **Verify the service**
   ```bash
   curl http://localhost:7000/health
   ```

### Docker Compose Configuration

The `docker-compose.yml` file defines the following services:

- **video-worker-controller**: The main application
- **mongodb**: MongoDB database
- **redis**: Redis for WebSocket pub/sub (optional)

### Scaling

To scale the controller for high availability:

1. Set up a load balancer (e.g., Nginx, Traefik)
2. Configure `REDIS_URL` for cross-instance WebSocket communication
3. Deploy multiple instances behind the load balancer

## 📊 Monitoring and Logging

### Logs
Logs are written to stdout in JSON format. You can configure the log level using the `LOG_LEVEL` environment variable.

### Metrics
Prometheus metrics are available at `/metrics`.

### Health Checks
- `/health`: Basic health check
- `/health/db`: Database health check
- `/health/redis`: Redis health check (if configured)

## 🔄 Recovery and Watchdog

The controller includes two background jobs:

1. **Recovery Job**: Runs every `RECOVERY_INTERVAL` seconds to:
   - Reassign dangling streams to healthy workers
   - Clean up stale resources

2. **Watchdog Job**: Runs every `WATCHDOG_INTERVAL` seconds to:
   - Detect and remove unresponsive workers
   - Trigger recovery for affected streams

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- Built with [FastAPI](https://fastapi.tiangolo.com/)
- Uses [Motor](https://motor.readthedocs.io/) for async MongoDB access
- Inspired by modern microservice architectures
   APP_NAME=VideoFlow-Controller
   APP_VERSION=1.0.0
   
   # Worker Settings
   WORKER_HEALTH_CHECK_INTERVAL=30
   WORKER_RECOVERY_INTERVAL=60
   
   # CORS Settings
   CORS_ORIGINS=["http://localhost:3000"]
   ```

### Running the Application

```bash
uvicorn app.main:create_app --reload
```

The API will be available at `http://localhost:8000`

## 🛠️ API Documentation

Once the application is running, you can access:

- **Interactive API Docs**: `http://localhost:8000/docs`
- **Alternative API Docs**: `http://localhost:8000/redoc`
- **Health Check**: `http://localhost:8000/health`

## 🏗️ Architecture

### Core Components

1. **API Layer**
   - FastAPI-based RESTful endpoints
   - Request validation using Pydantic models
   - Authentication and rate limiting

2. **Worker Management**
   - Worker registration and heartbeat tracking
   - Load balancing across available workers
   - Automatic worker health monitoring

3. **Task Management**
   - Task distribution and scheduling
   - Result collection and storage
   - Retry and failure handling

4. **Background Services**
   - Worker health watchdog
   - Task recovery system
   - Resource monitoring

## 📚 API Reference

### Workers

- `POST /api/workers/register` - Register a new worker
- `GET /api/workers` - List all registered workers
- `GET /api/workers/{worker_id}` - Get worker details
- `DELETE /api/workers/{worker_id}` - Remove a worker

### Streams

- `POST /api/streams` - Create a new video processing stream
- `GET /api/streams` - List all streams
- `GET /api/streams/{stream_id}` - Get stream details
- `DELETE /api/streams/{stream_id}` - Cancel a stream

## 🔍 Monitoring and Logging

The application includes comprehensive logging:

- Structured JSON logging for easy parsing
- Different log levels (INFO, WARNING, ERROR)
- Request/response logging
- Error tracking with stack traces

## 🧪 Testing

Run the test suite:

```bash
pytest
```

## 🤝 Contributing

Contributions are welcome! Please follow these steps:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 📧 Contact

For questions or support, please open an issue on GitHub.

---

<div align="center">
  Made with ❤️ by Nouman Ahsan
</div>

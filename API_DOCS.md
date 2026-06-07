# 🔌 Smart Cloud Pulse AI Monitor — API Documentation

This document provides a comprehensive reference for the REST API endpoints exposed by the **Smart Cloud Pulse AI Monitor** backend (`app.py`).

---

## 🔒 Authentication

Some endpoints require authentication. To authenticate, include the `X-API-Key` header with your request. The value must match the `API_KEY` defined in your `.env` file.

*   **Header Name**: `X-API-Key`
*   **Header Value**: `<your-api-key>`

---

## 📂 Complete Endpoint Reference

### 1. Ingest Metrics
*   **Endpoint**: `POST /api/metrics`
*   **Authentication**: Required (`X-API-Key`)
*   **Description**: Ingests a new metric snapshot from the local monitoring agent, processes features, evaluates severity, and runs the Isolation Forest prediction.
*   **Request Headers**:
    ```http
    X-API-Key: your_secret_api_key
    Content-Type: application/json
    ```
*   **Request JSON Body**:
    ```json
    {
      "timestamp": "2026-06-07T05:28:14",
      "cpu": 27.5,
      "ram": 64.7,
      "disk": 13.6,
      "network_in": 277868.81,
      "network_out": 56630.14,
      "processes": 279,
      "uptime": 568095.0
    }
    ```
*   **Response (201 Created)**:
    ```json
    {
      "success": true,
      "metric_id": 1054,
      "prediction": {
        "is_anomaly": false,
        "score": -0.4072,
        "severity": "Normal",
        "trained": true
      }
    }
    ```

---

### 2. Fetch Latest Metrics
*   **Endpoint**: `GET /api/metrics/latest`
*   **Authentication**: None
*   **Description**: Returns a chronological list of recent metric snapshots. Used by the dashboard to plot live charts.
*   **Query Parameters**:
    *   `limit` (Optional, Integer, default: `100`): Maximum number of records to return.
    *   `server_id` (Optional, Integer, default: `0`): ID of the server to fetch metrics for.
*   **Response (200 OK)**:
    ```json
    {
      "success": true,
      "metrics": [
        {
          "id": 1054,
          "timestamp": "2026-06-07T05:28:14",
          "cpu": 27.5,
          "ram": 64.7,
          "disk": 13.6,
          "network_in": 277868.81,
          "network_out": 56630.14,
          "processes": 279,
          "uptime": 568095.0,
          "server_id": 0
        }
      ]
    }
    ```

---

### 3. Fetch Recent Anomalies
*   **Endpoint**: `GET /api/anomalies`
*   **Authentication**: None
*   **Description**: Returns a list of the most recent anomaly logs stored in the database.
*   **Query Parameters**:
    *   `limit` (Optional, Integer, default: `50`): Maximum number of anomalies to return.
    *   `server_id` (Optional, Integer): ID of the server to filter by (omitted = all servers).
*   **Response (200 OK)**:
    ```json
    {
      "success": true,
      "anomalies": [
        {
          "id": 31,
          "timestamp": "2026-06-07 05:13:32",
          "server_id": 0,
          "anomaly_score": -0.6683,
          "severity": "High Anomaly",
          "probable_cause": "Abnormal System Behavior",
          "reasons": "[\"Elevated CPU: 81.4%\", \"AI Severity: High (Isolation Forest Score: -0.6683)\"]",
          "network_in_ratio": 1.0,
          "network_out_ratio": 0.55,
          "metric_values": "{\"cpu\": 81.4, \"ram\": 68.8, ...}"
        }
      ]
    }
    ```

---

### 4. Get Server Status & Model Info
*   **Endpoint**: `GET /api/status`
*   **Authentication**: None
*   **Description**: Retrieves status, details, and machine learning model details (such as whether it is trained, its contamination rate, and feature schema) for a specific server.
*   **Query Parameters**:
    *   `server_id` (Optional, Integer, default: `0`): ID of the target server.
*   **Response (200 OK)**:
    ```json
    {
      "success": true,
      "server_name": "VPS-Server-01",
      "server_ip": "127.0.0.1",
      "data": {
        "last_updated": "2026-06-07T05:28:14",
        "metric_id": 1054,
        "score": -0.4072,
        "severity": "Normal",
        "status": "normal",
        "total_metrics": 803,
        "model": {
          "is_trained": true,
          "contamination": 0.05,
          "feature_count": 16,
          "train_samples_required": 200,
          "model_path": "/path/to/isolation_forest_0.joblib"
        }
      }
    }
    ```

---

### 5. Manually Retrain Model
*   **Endpoint**: `POST /api/train`
*   **Authentication**: Required (`X-API-Key`)
*   **Description**: Manually triggers retraining of the Isolation Forest model on the stored metrics database for a specific server.
*   **Query Parameters**:
    *   `server_id` (Optional, Integer, default: `0`): Target server ID.
    *   `limit` (Optional, Integer, default: `200`): Maximum training sample count.
*   **Response (200 OK)**:
    ```json
    {
      "success": true,
      "message": "Model retrained successfully.",
      "samples": 200
    }
    ```

---

### 6. Get SMTP Diagnostics & Email Status
*   **Endpoint**: `GET /api/email-status`
*   **Authentication**: None
*   **Description**: Retrieves statistics about alerts sent today, SMTP configurations, and connection diagnostics.
*   **Response (200 OK)**:
    ```json
    {
      "success": true,
      "enabled": true,
      "connected": true,
      "verified": true,
      "sender": "12pratikraj1089@gmail.com",
      "receiver": "123pratikraj1089@gmail.com",
      "sent_today": 14,
      "failed_today": 0,
      "status_msg": "SMTP login verification successful"
    }
    ```

---

### 7. Send Test Email
*   **Endpoint**: `POST /api/test-email`
*   **Authentication**: None
*   **Description**: Sends a mock anomaly alert email to verify the configured SMTP credentials and setup.
*   **Response (200 OK)**:
    ```json
    {
      "success": true,
      "message": "Test email sent successfully to 123pratikraj1089@gmail.com!"
    }
    ```

---

### 8. List Registered Servers
*   **Endpoint**: `GET /api/servers`
*   **Authentication**: None
*   **Description**: Returns a list of all servers currently registered in the database, including the local server and added remote VPS instances.
*   **Response (200 OK)**:
    ```json
    {
      "success": true,
      "servers": [
        {
          "id": 0,
          "name": "pratik",
          "ip": "127.0.0.1",
          "port": 22,
          "username": "local"
        },
        {
          "id": 2,
          "name": "kamalArora",
          "ip": "192.168.1.100",
          "port": 22,
          "username": "ubuntu"
        }
      ]
    }
    ```

---

### 9. Register a New Server
*   **Endpoint**: `POST /api/servers`
*   **Authentication**: None
*   **Description**: Registers a new VPS configuration in the local database.
*   **Request JSON Body**:
    ```json
    {
      "name": "vps-staging",
      "ip": "192.168.1.101",
      "port": 22,
      "username": "admin"
    }
    ```
*   **Response (200 OK)**:
    ```json
    {
      "success": true,
      "server_id": 3
    }
    ```

---

### 10. Start Remote VPS Monitoring (SSH Connect)
*   **Endpoint**: `POST /api/server/connect`
*   **Authentication**: None
*   **Description**: Establishes SSH polling connection for the remote VPS, begins retrieving metrics, and runs classification pipelines.
*   **Request JSON Body**:
    ```json
    {
      "server_id": 2,
      "password": "vps_ssh_password"
    }
    ```
*   **Response (200 OK)**:
    ```json
    {
      "success": true,
      "message": "Connected successfully and started background SSH monitoring."
    }
    ```

---

### 11. Disconnect / Stop VPS Monitoring
*   **Endpoint**: `POST /api/server/disconnect`
*   **Authentication**: None
*   **Description**: Terminates active SSH connection threads and stops monitoring for the specified server.
*   **Request JSON Body**:
    ```json
    {
      "server_id": 2
    }
    ```
*   **Response (200 OK)**:
    ```json
    {
      "success": true,
      "message": "Server 2 disconnected successfully."
    }
    ```

---

### 12. Fetch Alert Logs
*   **Endpoint**: `GET /api/alerts`
*   **Authentication**: None
*   **Description**: Returns a log of all past critical alerts triggered and emailed.
*   **Response (200 OK)**:
    ```json
    {
      "success": true,
      "alerts": [
        {
          "id": 12,
          "timestamp": "2026-06-07 04:40:22",
          "server_id": 0,
          "severity": "High Anomaly",
          "score": -0.6683,
          "channel": "email",
          "sent_to": "123pratikraj1089@gmail.com"
        }
      ]
    }
    ```

---

### 13. API Health Check
*   **Endpoint**: `GET /api/health`
*   **Authentication**: None
*   **Description**: Simple diagnostic ping endpoint to verify that the Flask API server is online and running.
*   **Response (200 OK)**:
    ```json
    {
      "status": "healthy",
      "timestamp": "2026-06-07T13:53:12"
    }
    ```

# KPT Poller Pipeline

## Overview

Interceptor for KPT (Kyiv Public Transport) - REST API for route list + real-time positions from WebSocket.

## Architecture

```mermaid
sequenceDiagram
    participant Poller
    participant Browser as Patchright Browser
    participant CF as Cloudflare
    participant KPT as KPT Website
    participant WS as WebSocket Server
    participant Storage as JSONL Files

    Note over Poller,Storage: Initialization Phase
    Poller->>Browser: Start browser
    Browser->>KPT: Navigate to kpt.kyiv.ua
    KPT-->>CF: Cloudflare challenge
    CF-->>Browser: Turnstile CAPTCHA
    Browser->>CF: Solve challenge
    CF-->>Browser: Set cf_clearance cookie
    Browser-->>Poller: Extract cookies + user-agent

    Note over Poller,Storage: WebSocket Connection
    Poller->>WS: HTTP handshake (with cookies)
    WS-->>Poller: Session ID + ping interval
    Poller->>WS: Upgrade to WebSocket
    WS-->>Poller: Connection established
    Poller->>WS: Send probe (2probe)
    WS-->>Poller: Probe response (3probe)
    Poller->>WS: Complete upgrade (5, 40)

    Note over Poller,Storage: Data Collection Loop
    loop Every 5 seconds
        WS-->>Poller: Vehicle positions (CSV)
        Poller->>Storage: Write positions batch
    end

    loop Every 30 seconds
        Poller->>KPT: GET /api/route/list
        KPT-->>Poller: Routes JSON
        Poller->>Storage: Write routes record
    end

    Note over Poller,Storage: Cookie Refresh (on 403)
    KPT-->>Poller: HTTP 403 (expired)
    Poller->>Browser: Restart bypass
    Browser->>CF: New challenge
    CF-->>Browser: New cookies
    Browser-->>Poller: Updated cookies
    Poller->>WS: Reconnect with new cookies
```

## Data Flow

1. **Cloudflare Bypass** - Patchright to solve Turnstile CAPTCHA
2. **Cookie Extraction** - `cf_clearance` cookie - API access
3. **WebSocket Connect** - Socket.IO handshake + session ID
4. **Position Streaming** - Read real-time data in CSV
5. **Route Polling** - Periodic REST API calls - routes list

## Message Format

Vehicle position CSV: `vehicle_id,route_id,lat,lon,direction,flag,timestamp`
Example: `12585093,12583358,50.50963,30.64338,0,0,1769342268`

## Output Streaming

- `kpt_positions_YYYYMMDD.jsonl` - Vehicle positions
- `kpt_routes_YYYYMMDD.jsonl` - Route metadata

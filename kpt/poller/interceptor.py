# WebSocket interceptor script - captures Socket.IO messages
# injected into the browser context

WS_INTERCEPTOR_JS = """
(function() {
    'use strict';

    window.kptData = {
        positions: [],
        rawMessages: [],
        connectionCount: 0,
        lastUpdate: null,
        errors: []
    };

    function parseVehicleMessage(data) {
        // Format: "vehicle_id,route_id,lat,lon,direction,flag,timestamp"
        if (typeof data !== 'string') return null;

        const parts = data.split(',');
        if (parts.length !== 7) return null;

        const [vehicleId, routeId, lat, lon, direction, flag, timestamp] = parts;

        // Validate coordinates are in Kyiv area
        const latNum = parseFloat(lat);
        const lonNum = parseFloat(lon);
        if (latNum < 50.2 || latNum > 50.7 || lonNum < 30.2 || lonNum > 31.0) {
            return null;
        }

        return {
            vehicle_id: parseInt(vehicleId),
            route_id: parseInt(routeId),
            lat: latNum,
            lon: lonNum,
            direction: parseInt(direction),
            flag: parseInt(flag),
            timestamp: parseInt(timestamp)
        };
    }

    function handleWebSocketMessage(event) {
        const data = event.data;
        if (typeof data !== 'string') return;

        // Store raw message for debugging (limit to last 100)
        window.kptData.rawMessages.push({
            ts: Date.now(),
            data: data.substring(0, 200)
        });
        if (window.kptData.rawMessages.length > 100) {
            window.kptData.rawMessages.shift();
        }

        // Try parsing as vehicle position
        const parsed = parseVehicleMessage(data);
        if (parsed) {
            window.kptData.positions.push(parsed);
            window.kptData.lastUpdate = Date.now();
        }

        // Also handle Socket.IO format: 42["event", [...]]
        const match = data.match(/^42\\["(\\w+)",(.*)\\]$/s);
        if (match) {
            try {
                const eventName = match[1];
                const payload = JSON.parse(match[2]);
                if (eventName === 'vehicles' || eventName === 'positions') {
                    if (Array.isArray(payload)) {
                        payload.forEach(v => {
                            if (v.lat && v.lon) {
                                window.kptData.positions.push({
                                    vehicle_id: v.id || v.vehicle_id,
                                    route_id: v.route_id || v.routeId,
                                    lat: parseFloat(v.lat),
                                    lon: parseFloat(v.lon),
                                    direction: v.direction || 0,
                                    flag: v.flag || 0,
                                    timestamp: v.timestamp || Math.floor(Date.now() / 1000)
                                });
                            }
                        });
                        window.kptData.lastUpdate = Date.now();
                    }
                }
            } catch (e) {
                window.kptData.errors.push(e.message);
            }
        }
    }

    const OriginalWebSocket = window.WebSocket;
    window.WebSocket = function(url, protocols) {
        window.kptData.connectionCount++;
        console.log('[KPT] WebSocket connecting:', url);

        const socket = protocols
            ? new OriginalWebSocket(url, protocols)
            : new OriginalWebSocket(url);

        socket.addEventListener('message', handleWebSocketMessage);
        socket.addEventListener('error', function(e) {
            window.kptData.errors.push('WebSocket error: ' + e.message);
        });

        return socket;
    };

    window.WebSocket.prototype = OriginalWebSocket.prototype;
    window.WebSocket.CONNECTING = OriginalWebSocket.CONNECTING;
    window.WebSocket.OPEN = OriginalWebSocket.OPEN;
    window.WebSocket.CLOSING = OriginalWebSocket.CLOSING;
    window.WebSocket.CLOSED = OriginalWebSocket.CLOSED;

    console.log('[KPT] WebSocket interceptor installed');
})();
"""

# extract and clear positions atomically
EXTRACT_POSITIONS_JS = """
(() => {
    const positions = window.kptData.positions || [];
    window.kptData.positions = [];
    return positions;
})()
"""

# get WebSocket statistics
GET_WS_STATS_JS = """
(() => ({
    connectionCount: window.kptData?.connectionCount || 0,
    lastUpdate: window.kptData?.lastUpdate,
    rawMessageCount: window.kptData?.rawMessages?.length || 0,
    errorCount: window.kptData?.errors?.length || 0,
    recentRaw: (window.kptData?.rawMessages || []).slice(-5)
}))()
"""

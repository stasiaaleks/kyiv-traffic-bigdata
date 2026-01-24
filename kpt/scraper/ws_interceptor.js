/**
 * WebSocket Interceptor for KPT Kyiv Public Transport
 *
 * Patches the WebSocket constructor to capture all Socket.IO messages
 * and store them in window.kptData for retrieval via Selenium.
 *
 * Socket.IO message format: <type><json>
 * - Type 0: CONNECT
 * - Type 2: PING
 * - Type 3: PONG
 * - Type 4: MESSAGE (40 = connect, 42 = event)
 *
 * Example: 42["vehicles", [{id: 1, lat: 50.45, lng: 30.52}, ...]]
 */

(function() {
    'use strict';

    const SOCKET_IO_EVENT_PREFIX = /^\d+/;
    const JSON_ARRAY_PATTERN = /^\d+(\[.*\])$/s;

    window.kptData = {
        messages: [],
        vehicles: [],
        routes: [],
        errors: [],
        connectionCount: 0,
        lastUpdate: null
    };

    function parseSocketIOMessage(rawData) {
        if (typeof rawData !== 'string') {
            return null;
        }

        if (!SOCKET_IO_EVENT_PREFIX.test(rawData)) {
            return null;
        }

        const match = rawData.match(JSON_ARRAY_PATTERN);
        if (!match) {
            return null;
        }

        try {
            const parsed = JSON.parse(match[1]);
            if (Array.isArray(parsed) && parsed.length >= 2) {
                return {
                    event: parsed[0],
                    payload: parsed[1]
                };
            }
        } catch (e) {
            window.kptData.errors.push({
                timestamp: new Date().toISOString(),
                error: e.message,
                data: rawData.substring(0, 100)
            });
        }

        return null;
    }

    function handleVehicleData(payload) {
        if (!Array.isArray(payload)) {
            return;
        }
        window.kptData.vehicles = payload;
        console.log('[KPT] Vehicles updated:', payload.length);
    }

    function handleRouteData(payload) {
        if (!Array.isArray(payload)) {
            return;
        }
        window.kptData.routes = payload;
        console.log('[KPT] Routes updated:', payload.length);
    }

    function recordMessage(eventName, payload) {
        window.kptData.messages.push({
            timestamp: new Date().toISOString(),
            event: eventName,
            itemCount: Array.isArray(payload) ? payload.length : 1
        });
        window.kptData.lastUpdate = new Date().toISOString();
    }

    function handleWebSocketMessage(event) {
        const parsed = parseSocketIOMessage(event.data);
        if (!parsed) {
            return;
        }

        const { event: eventName, payload } = parsed;
        recordMessage(eventName, payload);

        switch (eventName) {
            case 'vehicles':
            case 'positions':
                handleVehicleData(payload);
                break;
            case 'routes':
                handleRouteData(payload);
                break;
            default:
                console.log('[KPT] Event:', eventName);
        }
    }

    function createInterceptedWebSocket(OriginalWebSocket) {
        return function(url, protocols) {
            window.kptData.connectionCount++;
            console.log('[KPT] WebSocket connecting:', url);

            const socket = new OriginalWebSocket(url, protocols);
            socket.addEventListener('message', handleWebSocketMessage);

            socket.addEventListener('close', function(event) {
                console.log('[KPT] WebSocket closed:', event.code, event.reason);
            });

            socket.addEventListener('error', function(event) {
                console.error('[KPT] WebSocket error:', event);
                window.kptData.errors.push({
                    timestamp: new Date().toISOString(),
                    error: 'WebSocket error',
                    url: url
                });
            });

            return socket;
        };
    }

    // Patch WebSocket constructor
    const OriginalWebSocket = window.WebSocket;
    const InterceptedWebSocket = createInterceptedWebSocket(OriginalWebSocket);

    // Preserve prototype for instanceof checks
    InterceptedWebSocket.prototype = OriginalWebSocket.prototype;
    InterceptedWebSocket.CONNECTING = OriginalWebSocket.CONNECTING;
    InterceptedWebSocket.OPEN = OriginalWebSocket.OPEN;
    InterceptedWebSocket.CLOSING = OriginalWebSocket.CLOSING;
    InterceptedWebSocket.CLOSED = OriginalWebSocket.CLOSED;

    window.WebSocket = InterceptedWebSocket;

    console.log('[KPT] WebSocket interceptor installed');
})();

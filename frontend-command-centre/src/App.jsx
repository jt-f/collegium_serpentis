import React, { useState, useEffect, useRef, useCallback } from 'react';
import Layout from './components/Layout';
import ClientTable from './components/ClientTable';
import ChatWindow from './components/ChatWindow';
import StatusOverview from './components/StatusOverview';

const generateRandomString = (length = 8) => {
    const characters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    let result = '';
    for (let i = 0; i < length; i++) {
        result += characters.charAt(Math.floor(Math.random() * characters.length));
    }
    return result;
};

const generateFrontendClientId = () => `react-fe-${generateRandomString(6)}`;
const generateFrontendClientName = () => `human-${generateRandomString(4)}`;

// WebSocket URL
const WS_URL =
    process.env.NODE_ENV === 'development'
        ? `ws://${window.location.hostname}:8000/ws`
        : `ws://${window.location.host}/ws`;

function App() {
    const [clients, setClients] = useState({});
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState(null);
    const [redisStatus, setRedisStatus] = useState('unknown');
    const [wsStatus, setWsStatus] = useState('disconnected');
    const frontendClientId = useRef(generateFrontendClientId());
    const frontendClientName = useRef(generateFrontendClientName());

    const websocket = useRef(null);
    const reconnectInterval = useRef(null);

    const connectWebSocket = useCallback(() => {
        if (websocket.current && websocket.current.readyState === WebSocket.OPEN) {
            console.log("WebSocket already connected.");
            return;
        }
        if (reconnectInterval.current) {
            clearInterval(reconnectInterval.current);
            reconnectInterval.current = null;
        }

        console.log("Attempting to connect to WebSocket:", WS_URL);
        setWsStatus('connecting');
        const ws = new WebSocket(WS_URL);
        websocket.current = ws;

        ws.onopen = () => {
            console.log("WebSocket connected successfully to:", WS_URL);
            setWsStatus('connected');
            setError(null);
            if (reconnectInterval.current) {
                clearInterval(reconnectInterval.current);
                reconnectInterval.current = null;
            }
            ws.send(JSON.stringify({
                client_id: frontendClientId.current,
                status: {
                    client_name: frontendClientName.current,
                    client_role: "frontend",
                    client_type: "react_dashboard",
                    connected_at: new Date().toISOString(),
                }
            }));
        };

        ws.onmessage = (event) => {
            try {
                const message = JSON.parse(event.data);
                console.log("WebSocket message received:", message);

                if (message.type === 'all_clients_update' && message.data) {
                    setClients(message.data.clients || {});
                    setRedisStatus(message.data.redis_status || 'unknown');
                } else if (message.client_id && message.status) { // Single client update
                    setClients(prevClients => ({
                        ...prevClients,
                        [message.client_id]: {
                            ...(prevClients[message.client_id] || {}),
                            ...message.status,
                            client_id: message.client_id,
                            recentlyUpdated: true // Add flag for animation
                        }
                    }));
                    // Remove the flag after a short period
                    setTimeout(() => {
                        setClients(prevClients => {
                            if (prevClients[message.client_id]) {
                                const { recentlyUpdated, ...rest } = prevClients[message.client_id];
                                return {
                                    ...prevClients,
                                    [message.client_id]: rest
                                };
                            }
                            return prevClients;
                        });
                    }, 1500); // Animation duration + buffer
                } else if (message.type === 'client_disconnected' && message.client_id) {
                    setClients(prevClients => {
                        const newClients = { ...prevClients };
                        if (newClients[message.client_id]) {
                            // Option 1: Mark as disconnected (if server doesn't send full status)
                            // newClients[message.client_id] = {
                            //    ...newClients[message.client_id],
                            //    connected: 'false',
                            //    disconnect_time: new Date().toISOString(), 
                            //    status_detail: 'Disconnected (event)'
                            // };
                            // Option 2: Remove from list (if server broadcasts deletions)
                            delete newClients[message.client_id];
                        }
                        return newClients;
                    });
                } else if (message.redis_status) {
                    setRedisStatus(message.redis_status);
                }

                if (message.result === 'message_processed' && message.client_id === frontendClientId.current) {
                    console.log("Frontend registration acknowledged by server.");
                    if (message.redis_status) setRedisStatus(message.redis_status);
                } else if (message.type === 'control_response') {
                    console.log('Control response received:', message);
                    if (message.status === 'error') {
                        console.error(`Control action '${message.action}' for client '${message.target_client_id}' failed: ${message.message}`);
                        // Optionally, set an error state to display to the user
                        // setError(`Action ${message.action} on ${message.target_client_id} failed: ${message.message}`);
                    } else {
                        console.log(`Control action '${message.action}' for client '${message.target_client_id}' was successful: ${message.message}`);
                        // Optionally, show a success notification
                    }
                }

                if (isLoading && (message.type === 'all_clients_update' || (message.client_id && message.status))) {
                    setIsLoading(false);
                }

            } catch (e) {
                console.error('Failed to parse WebSocket message:', e);
            }
        };

        ws.onclose = (event) => {
            console.log(`WebSocket disconnected: ${event.code} - ${event.reason}`);
            setWsStatus('disconnected');
            websocket.current = null;
            if (!reconnectInterval.current && event.code !== 1000) {
                console.log("Attempting to reconnect WebSocket in 5 seconds...");
                reconnectInterval.current = setInterval(() => {
                    console.log("Retrying WebSocket connection...");
                    connectWebSocket(); // This call within onclose is for retries
                }, 5000);
            }
        };

        ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            setWsStatus('error');
            websocket.current = null;
        };
    }, [WS_URL]); // Added WS_URL as it's an external variable used inside.

    // Effect to initiate WebSocket connection on mount
    useEffect(() => {
        connectWebSocket();

        // Cleanup function to close WebSocket when component unmounts
        return () => {
            if (websocket.current) {
                console.log("Closing WebSocket connection on component unmount.");
                websocket.current.close(1000, "Component unmounting");
            }
            if (reconnectInterval.current) {
                clearInterval(reconnectInterval.current);
            }
        };
    }, [connectWebSocket]); // Dependency on connectWebSocket (which is memoized)

    // Effect for HTTP Polling (as a fallback or for initial load)
    useEffect(() => {
        const fetchClients = async () => {
            if (Object.keys(clients).length === 0 && wsStatus !== 'connected') {
                setIsLoading(true);
            }
            try {
                const apiUrl = process.env.NODE_ENV === 'development'
                    ? `http://${window.location.hostname}:8000/statuses`
                    : `/statuses`;
                const response = await fetch(apiUrl);
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                const data = await response.json();
                // Only set from HTTP if WebSocket isn't connected or hasn't provided data yet
                if (wsStatus !== 'connected' || Object.keys(clients).length === 0) {
                    setClients(data.clients || {});
                    if (isLoading && Object.keys(data.clients || {}).length > 0) setIsLoading(false);
                }
                setRedisStatus(data.redis_status || 'unknown');
                if (data.error_redis) {
                    console.warn("Redis error from server (HTTP poll):", data.error_redis);
                }
                if (wsStatus !== 'connected') setError(null); // Clear HTTP error if poll succeeds and WS is down
            } catch (e) {
                console.error("Failed to fetch client statuses (HTTP poll):", e);
                if (wsStatus !== 'connected') {
                    setError(`Could not load system status: ${e.message}`);
                    if (isLoading) setIsLoading(false);
                }
            }
        };
        // Initial fetch if WebSocket isn't immediately connecting/connected
        if (wsStatus === 'disconnected' || wsStatus === 'error') {
            fetchClients();
        }
        const intervalId = setInterval(() => {
            if (wsStatus !== 'connected') { // Poll only if WebSocket is not connected
                fetchClients();
            }
        }, 10000);

        return () => clearInterval(intervalId);
    }, [wsStatus, isLoading]); // Re-evaluate polling based on wsStatus and isLoading

    // Client Action Handlers
    const handleClientAction = (clientId, action) => {
        console.log(`Attempting to ${action} client via WebSocket: ${clientId}`);
        if (websocket.current && websocket.current.readyState === WebSocket.OPEN) {
            const messageId = `${Date.now()}-${generateRandomString(4)}`;
            const controlMessage = {
                type: "control",
                action: action,
                target_client_id: clientId,
                message_id: messageId
            };
            try {
                websocket.current.send(JSON.stringify(controlMessage));
                console.log(`Sent ${action} command for ${clientId}, message_id: ${messageId}`);
                // The response will be handled by the onmessage handler
            } catch (err) {
                console.error(`Error sending ${action} command for ${clientId} via WebSocket:`, err);
                setError(`Failed to send ${action} command: WebSocket error. Please check connection.`);
            }
        } else {
            console.error(`Cannot ${action} client ${clientId}: WebSocket is not connected.`);
            setError('WebSocket is not connected. Please check connection and try again.');
            // Optionally, try to reconnect or notify user more prominently
            if (!websocket.current || websocket.current.readyState === WebSocket.CLOSED) {
                connectWebSocket(); // Attempt to reconnect if fully closed
            }
        }
    };

    const messages = [
        { id: 1, sender: 'client-1', text: 'Obstacle detected at K2' },
        { id: 2, sender: 'Operator', text: 'Acknowledge Alpha Rover, rerouting.' },
        { id: 3, sender: 'client-3', text: 'Low battery warning.' },
    ];

    return (
        <Layout wsStatus={wsStatus}> {/* Pass wsStatus to Layout for potential display */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 p-6 bg-slate-900 text-slate-50 min-h-screen">
                <div className="lg:col-span-2 flex flex-col gap-6">
                    <StatusOverview
                        clients={clients}
                        isLoading={isLoading}
                        error={error}
                        redisStatus={redisStatus}
                        wsStatus={wsStatus}
                    />
                    <ClientTable
                        clients={clients}
                        isLoading={isLoading}
                        error={error}
                        onClientAction={handleClientAction}
                    />
                </div>
                <div className="lg:col-span-1">
                    <ChatWindow messages={messages} />
                </div>
            </div>
        </Layout>
    );
}

export default App; 
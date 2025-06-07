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
    const [isRegistered, setIsRegistered] = useState(false); // Track if frontend is registered with server
    const [chatMessages, setChatMessages] = useState([
        { id: 1, sender: 'client-1', text: 'Obstacle detected at K2', timestamp: new Date().toISOString(), acknowledged: true },
        { id: 2, sender: 'Operator', text: 'Acknowledge Alpha Rover, rerouting.', timestamp: new Date().toISOString(), acknowledged: true },
        { id: 3, sender: 'client-3', text: 'Low battery warning.', timestamp: new Date().toISOString(), acknowledged: true },
    ]);
    const [selectedTargetId, setSelectedTargetId] = useState(null); // null means 'all'
    const frontendClientId = useRef(generateFrontendClientId());
    const frontendClientName = useRef(generateFrontendClientName());

    const websocket = useRef(null);
    const reconnectInterval = useRef(null);

    const connectWebSocket = useCallback(() => {
        // If there's an existing WebSocket, close it first
        if (websocket.current) {
            if (websocket.current.readyState === WebSocket.OPEN) {
                console.log("WebSocket already connected.");
                return;
            }
            if (websocket.current.readyState === WebSocket.CONNECTING) {
                console.log("WebSocket is already connecting, waiting...");
                return;
            }
            // Close any existing connection that's in a bad state
            try {
                websocket.current.close();
                websocket.current = null; // Clear reference after closing
                console.log("Closed and cleared existing WebSocket reference");
            } catch (e) {
                console.warn("Error closing existing WebSocket:", e);
                websocket.current = null; // Clear reference even if close fails
            }
        }

        if (reconnectInterval.current) {
            clearInterval(reconnectInterval.current);
            reconnectInterval.current = null;
        }

        console.log("Attempting to connect to WebSocket:", WS_URL);
        setWsStatus('connecting');
        setIsRegistered(false); // Reset registration status
        const ws = new WebSocket(WS_URL);
        websocket.current = ws;

        ws.onopen = () => {
            console.log("WebSocket onopen event fired for:", WS_URL);

            // Verify the WebSocket is actually ready before proceeding
            if (ws.readyState !== WebSocket.OPEN) {
                console.error("WebSocket onopen fired but readyState is not OPEN:", ws.readyState);
                return;
            }

            console.log("WebSocket verified as OPEN, proceeding with initialization");
            setError(null);

            // Clear any existing reconnect interval if connection is successful
            if (reconnectInterval.current) {
                clearInterval(reconnectInterval.current);
                reconnectInterval.current = null;
            }

            // Set status immediately since we've verified the connection is open
            setWsStatus('connected');
            console.log("WebSocket status set to connected");

            ws.send(JSON.stringify({
                client_id: frontendClientId.current,
                type: "register",
                status: {
                    client_name: frontendClientName.current,
                    client_role: "frontend",
                    client_type: "react_dashboard",
                    client_state: "running",
                    client_registration_timestamp: new Date().toISOString(),
                }
            }));

            // Start sending heartbeats
            // Ensure only one heartbeat interval is active
            if (ws.heartbeatInterval) clearInterval(ws.heartbeatInterval);
            ws.heartbeatInterval = setInterval(() => {
                if (ws.readyState === WebSocket.OPEN) {
                    console.log("Sending frontend heartbeat");
                    ws.send(JSON.stringify({
                        type: "heartbeat",
                        client_id: frontendClientId.current,
                        timestamp: new Date().toISOString()
                    }));
                }
            }, 30000); // Send heartbeat every 30 seconds
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

                if (message.type === 'registration_complete' && message.client_id === frontendClientId.current) {
                    console.log("Frontend registration completed by server.");
                    setIsRegistered(true); // Mark as fully registered
                    console.log("🎉 Frontend is now fully ready to send chat messages!");
                    if (message.redis_status) setRedisStatus(message.redis_status);
                } else if (message.result === 'message_processed' && message.client_id === frontendClientId.current) {
                    console.log("Frontend registration acknowledged by server.");
                    setIsRegistered(true); // Fallback for older server versions
                    console.log("🎉 Frontend is now fully ready to send chat messages!");
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
                } else if (message.type === 'chat_ack') {
                    console.log('Chat acknowledgment received:', message);
                    setChatMessages(prevMessages =>
                        prevMessages.map(msg =>
                            msg.id === message.message_id || (msg.tempId && msg.tempId === message.message_id)
                                ? {
                                    ...msg,
                                    acknowledged: true,
                                    timestamp: message.timestamp,
                                    id: message.message_id || msg.id
                                }
                                : msg
                        )
                    );
                } else if (message.type === 'chat') {
                    console.log('Chat message received from another frontend:', message);
                    const newIncomingMessage = {
                        id: `${message.client_id}-${message.timestamp}`, // Unique ID based on sender and timestamp
                        sender: message.client_id,
                        text: message.message, // Use 'text' to match own messages
                        target_id: message.target_id, // Include target_id if present
                        timestamp: message.timestamp,
                        acknowledged: true, // Incoming messages are already "acknowledged" by nature
                        isOwnMessage: false // Flag to differentiate from own messages
                    };
                    // Add incoming chat message from other frontend to chat messages
                    setChatMessages(prevMessages => [...prevMessages, newIncomingMessage]);
                }

                if (isLoading && (message.type === 'all_clients_update' || (message.client_id && message.status))) {
                    setIsLoading(false);
                }

            } catch (e) {
                console.error('Failed to parse WebSocket message:', e);
            }
        };

        ws.onclose = (event) => {
            console.warn("WebSocket disconnected:", event.code, event.reason);
            setWsStatus('disconnected');
            setIsRegistered(false); // Reset registration status on disconnect

            // Clear the WebSocket reference when it closes
            if (websocket.current === ws) {
                websocket.current = null;
                console.log("Cleared WebSocket reference after close");
            }

            // Stop heartbeats
            if (ws.heartbeatInterval) {
                clearInterval(ws.heartbeatInterval);
                ws.heartbeatInterval = null;
            }

            // Attempt to reconnect if not a clean close and no reconnect interval is already set
            if (event.code !== 1000 && !reconnectInterval.current) {
                console.log("Attempting to reconnect WebSocket in 5 seconds...");
                reconnectInterval.current = setInterval(() => {
                    console.log("Retrying WebSocket connection...");
                    // No need to call connectWebSocket() directly if it's handled by useEffect dependency
                    // Forcing a re-render or state change that triggers useEffect might be cleaner
                    // However, for simplicity here, explicitly calling it, ensuring connectWebSocket is stable
                    if (!websocket.current || websocket.current.readyState === WebSocket.CLOSED) {
                        connectWebSocket();
                    }
                }, 5000);
            }
        };

        ws.onerror = (error) => {
            console.error('WebSocket error:', error);
            setWsStatus('error');
            setIsRegistered(false); // Reset registration on error

            // Clear the WebSocket reference on error
            if (websocket.current === ws) {
                websocket.current = null;
                console.log("Cleared WebSocket reference after error");
            }
        };
    }, [WS_URL]); // Added WS_URL as it's an external variable used inside.

    // Effect to initiate WebSocket connection on mount
    useEffect(() => {
        console.log("Component mounted, initiating WebSocket connection");
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
    }, [connectWebSocket]); // Restore dependency on connectWebSocket

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

    // Health check effect to detect WebSocket state mismatches
    useEffect(() => {
        const healthCheckInterval = setInterval(() => {
            // Only check if UI thinks we're connected
            if (wsStatus === 'connected' || isRegistered) {
                if (!websocket.current || websocket.current.readyState !== WebSocket.OPEN) {
                    console.warn("🏥 Health check failed: WebSocket state mismatch detected");
                    setWsStatus('disconnected');
                    setIsRegistered(false);
                    connectWebSocket();
                }
            }
        }, 5000); // Check every 5 seconds

        return () => clearInterval(healthCheckInterval);
    }, [wsStatus, isRegistered, connectWebSocket]);

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

    // Chat Message Handler
    const sendChatMessage = (messageText, targetId = null) => {
        if (!messageText.trim()) return;

        const messageId = `chat-${Date.now()}-${generateRandomString(6)}`;
        const timestamp = new Date().toISOString();

        // Add message to local state immediately with pending status
        const newMessage = {
            id: messageId,
            tempId: messageId, // Temporary ID for matching with acknowledgment
            sender: 'Operator',
            text: messageText.trim(),
            timestamp: timestamp,
            acknowledged: false,
            isOwnMessage: true,
            targetId: targetId // Store target for display purposes
        };

        setChatMessages(prevMessages => [...prevMessages, newMessage]);

        // Enhanced debugging for WebSocket state
        const wsExists = !!websocket.current;
        const wsReadyState = websocket.current?.readyState;
        const wsReadyStateString = wsReadyState === WebSocket.CONNECTING ? 'CONNECTING' :
            wsReadyState === WebSocket.OPEN ? 'OPEN' :
                wsReadyState === WebSocket.CLOSING ? 'CLOSING' :
                    wsReadyState === WebSocket.CLOSED ? 'CLOSED' : 'UNKNOWN';

        console.log(`Chat send attempt - wsExists: ${wsExists}, readyState: ${wsReadyState} (${wsReadyStateString}), wsStatus: ${wsStatus}, isRegistered: ${isRegistered}`);

        // Detect state mismatch: UI thinks we're connected but WebSocket is null/closed
        if ((wsStatus === 'connected' || isRegistered) && (!websocket.current || websocket.current.readyState !== WebSocket.OPEN)) {
            console.warn("🚨 State mismatch detected! UI thinks connected but WebSocket is not ready. Attempting reconnection...");
            setWsStatus('disconnected');
            setIsRegistered(false);
            connectWebSocket();
        }

        // Additional safety check: verify all conditions are met in real-time
        const canSendNow = websocket.current &&
            websocket.current.readyState === WebSocket.OPEN &&
            isRegistered &&
            wsStatus === 'connected';

        if (canSendNow) {
            const chatMessage = {
                type: "chat",
                client_id: frontendClientId.current,
                message: messageText.trim(),
                message_id: messageId,
                timestamp: timestamp,
                ...(targetId && { target_id: targetId }) // Include target_id only if specified
            };

            try {
                websocket.current.send(JSON.stringify(chatMessage));
                console.log(`Sent chat message, message_id: ${messageId}`, chatMessage);
            } catch (err) {
                console.error(`Error sending chat message via WebSocket:`, err);
                // Mark message as failed
                setChatMessages(prevMessages =>
                    prevMessages.map(msg =>
                        msg.tempId === messageId
                            ? { ...msg, acknowledged: false, error: true }
                            : msg
                    )
                );
                setError(`Failed to send chat message: WebSocket error.`);
            }
        } else {
            const errorReason = !websocket.current ? 'WebSocket reference is null' :
                websocket.current.readyState !== WebSocket.OPEN ? `WebSocket state is ${wsReadyStateString} (not OPEN)` :
                    wsStatus !== 'connected' ? `UI status is '${wsStatus}' (not 'connected')` :
                        !isRegistered ? 'Frontend not yet registered with server' :
                            'Unknown error';

            console.error(`Cannot send chat message: ${errorReason}.`);
            // Mark message as failed
            setChatMessages(prevMessages =>
                prevMessages.map(msg =>
                    msg.tempId === messageId
                        ? { ...msg, acknowledged: false, error: true }
                        : msg
                )
            );
            setError(`${errorReason}. Please wait and try again.`);

            if (!websocket.current || websocket.current.readyState === WebSocket.CLOSED) {
                connectWebSocket(); // Attempt to reconnect if fully closed
            }
        }
    };

    // Target Selection Handler
    const handleTargetChange = (targetId) => {
        setSelectedTargetId(targetId);
    };

    // Handle envelope icon click in client table
    const handleSelectForMessage = (clientId) => {
        setSelectedTargetId(clientId);
    };

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
                        onSelectForMessage={handleSelectForMessage}
                    />
                </div>
                <div className="lg:col-span-1">
                    <ChatWindow
                        messages={chatMessages}
                        onSendMessage={sendChatMessage}
                        wsStatus={wsStatus}
                        isRegistered={isRegistered}
                        clients={clients}
                        selectedTargetId={selectedTargetId}
                        onTargetChange={handleTargetChange}
                    />
                </div>
            </div>
        </Layout>
    );
}

export default App; 
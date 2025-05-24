import { ref, computed } from 'vue'
import { defineStore } from 'pinia'

export const useWebSocketStore = defineStore('websocket', () => {
    // Connection state
    const ws = ref(null)
    const connectionStatus = ref('disconnected') // disconnected, connecting, connected, error
    const reconnectAttempts = ref(0)
    const maxReconnectAttempts = 5

    // Data state
    const clients = ref(new Map())
    const redisStatus = ref('unknown')
    const lastUpdate = ref(null)

    // Frontend client ID for identification
    const frontendClientId = ref(`fe-${Math.random().toString(36).substring(2, 9)}`)

    // Computed properties
    const clientsList = computed(() => Array.from(clients.value.values()))
    const connectedClientsCount = computed(() =>
        clientsList.value.filter(client => client.connected === 'true').length
    )
    const isConnected = computed(() => connectionStatus.value === 'connected')

    // WebSocket URL - adjust for production if needed
    const wsUrl = computed(() => {
        // In development, Vite proxy will handle the WebSocket connection
        // In production, use the same host as the frontend
        const isDevelopment = import.meta.env.DEV

        if (isDevelopment) {
            // Use relative URL which will be proxied by Vite
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
            const host = window.location.host
            return `${protocol}//${host}/ws`
        } else {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
            const host = window.location.host
            return `${protocol}//${host}/ws`
        }
    })

    function connect() {
        if (ws.value?.readyState === WebSocket.OPEN) {
            return
        }

        connectionStatus.value = 'connecting'
        console.log(`Attempting to connect to WebSocket: ${wsUrl.value}`)

        try {
            ws.value = new WebSocket(wsUrl.value)

            ws.value.onopen = () => {
                console.log('WebSocket connected successfully to:', wsUrl.value)
                connectionStatus.value = 'connected'
                reconnectAttempts.value = 0

                // Send initial frontend registration
                sendMessage({
                    client_id: frontendClientId.value,
                    status: {
                        client_type: 'frontend',
                        connected_at: new Date().toISOString()
                    }
                })
            }

            ws.value.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data)
                    handleMessage(data)
                } catch (error) {
                    console.error('Failed to parse WebSocket message:', error)
                }
            }

            ws.value.onclose = (event) => {
                console.log(`WebSocket disconnected: ${event.code} - ${event.reason}`)
                connectionStatus.value = 'disconnected'
                ws.value = null

                // Auto-reconnect with exponential backoff
                if (reconnectAttempts.value < maxReconnectAttempts) {
                    const delay = Math.pow(2, reconnectAttempts.value) * 1000
                    reconnectAttempts.value++

                    console.log(`Reconnecting in ${delay}ms (attempt ${reconnectAttempts.value}/${maxReconnectAttempts})`)
                    setTimeout(() => {
                        console.log(`Reconnecting attempt ${reconnectAttempts.value}...`)
                        connect()
                    }, delay)
                } else {
                    console.error('Max reconnection attempts reached')
                    connectionStatus.value = 'error'
                }
            }

            ws.value.onerror = (error) => {
                console.error('WebSocket error:', error)
                console.error('Failed to connect to:', wsUrl.value)
                connectionStatus.value = 'error'
            }

        } catch (error) {
            console.error('Failed to create WebSocket connection:', error)
            console.error('WebSocket URL was:', wsUrl.value)
            connectionStatus.value = 'error'
        }
    }

    function disconnect() {
        if (ws.value) {
            reconnectAttempts.value = maxReconnectAttempts // Prevent auto-reconnect
            ws.value.close()
            ws.value = null
        }
        connectionStatus.value = 'disconnected'
    }

    function sendMessage(message) {
        if (ws.value?.readyState === WebSocket.OPEN) {
            ws.value.send(JSON.stringify(message))
        } else {
            console.warn('WebSocket not connected, cannot send message:', message)
        }
    }

    function handleMessage(data) {
        lastUpdate.value = new Date().toISOString()

        // Handle server acknowledgments and status updates
        if (data.result === 'message_processed') {
            console.log('Message processed by server:', data)
            if (data.redis_status) {
                redisStatus.value = data.redis_status
            }
        }

        // Handle client status updates (this would come from server broadcasts)
        if (data.client_id && data.status) {
            updateClientData(data.client_id, data.status, true) // Real-time update
        }

        // Handle Redis status updates
        if (data.redis_status) {
            redisStatus.value = data.redis_status
        }
    }

    function updateClientData(clientId, statusData, fromRealTimeUpdate = false) {
        const existingClient = clients.value.get(clientId) || {}

        // Check if the data has actually changed (excluding lastUpdate field)
        const dataChanged = hasClientDataChanged(existingClient, statusData)

        const updatedClient = {
            ...existingClient,
            id: clientId,
            ...statusData
        }

        // Only update lastUpdate if:
        // 1. This is a real-time WebSocket update, OR
        // 2. The data has actually changed, OR  
        // 3. We're seeing this client for the first time
        if (fromRealTimeUpdate || dataChanged || !existingClient.id) {
            // Use the client's timestamp if available, otherwise current time
            updatedClient.lastUpdate = statusData.timestamp || new Date().toISOString()
        } else {
            // Preserve the existing lastUpdate time if data hasn't changed
            updatedClient.lastUpdate = existingClient.lastUpdate || new Date().toISOString()
        }

        // Format for display
        updatedClient.status = getDisplayStatus(updatedClient)
        updatedClient.lastSeen = formatLastSeen(updatedClient)
        updatedClient.details = getClientDetails(updatedClient)
        updatedClient.canPause = updatedClient.connected === 'true' &&
            updatedClient.client_state !== 'paused'
        updatedClient.canResume = updatedClient.connected === 'true' &&
            updatedClient.client_state === 'paused'
        updatedClient.canDisconnect = updatedClient.connected === 'true'

        clients.value.set(clientId, updatedClient)
    }

    function hasClientDataChanged(existingClient, newData) {
        // Compare relevant fields to see if data has actually changed
        const relevantFields = [
            'connected', 'client_state', 'cpu_usage', 'memory_usage',
            'timestamp', 'connect_time', 'disconnect_time', 'status_detail'
        ]

        return relevantFields.some(field => {
            const oldValue = existingClient[field]
            const newValue = newData[field]
            return oldValue !== newValue
        })
    }

    function getDisplayStatus(client) {
        if (client.connected === 'false') return 'Disconnected'
        if (client.client_state === 'paused') return 'Paused'
        if (client.connected === 'true') return 'Connected'
        return 'Unknown'
    }

    function formatLastSeen(client) {
        if (client.connected === 'false' && client.disconnect_time) {
            return `Disconnected: ${new Date(client.disconnect_time).toLocaleString()}`
        }

        if (client.client_state === 'paused' && client.lastUpdate) {
            return `Paused since: ${new Date(client.lastUpdate).toLocaleString()}`
        }

        if (client.lastUpdate) {
            const lastUpdateTime = new Date(client.lastUpdate)
            const now = new Date()
            const diffMinutes = Math.floor((now - lastUpdateTime) / (1000 * 60))

            if (diffMinutes < 1) {
                return 'Active now'
            } else if (diffMinutes < 60) {
                return `Last seen: ${diffMinutes} minute${diffMinutes === 1 ? '' : 's'} ago`
            } else {
                return `Last seen: ${lastUpdateTime.toLocaleString()}`
            }
        }

        if (client.connect_time) {
            return `Connected: ${new Date(client.connect_time).toLocaleString()}`
        }

        return 'Unknown'
    }

    function getClientDetails(client) {
        const details = []

        if (client.cpu_usage) details.push(`CPU: ${client.cpu_usage}`)
        if (client.memory_usage) details.push(`RAM: ${client.memory_usage}`)
        if (client.client_state && client.client_state !== 'running') {
            details.push(`State: ${client.client_state}`)
        }
        if (client.client_type) details.push(`Type: ${client.client_type}`)

        return details.length > 0 ? details.join(', ') : 'No details'
    }

    // Helper function to get the correct base URL for API calls
    function getBaseUrl() {
        // In development, use relative URLs which will be proxied by Vite
        // In production, use relative URLs (same origin)
        return ''
    }

    // Client control actions
    async function pauseClient(clientId) {
        try {
            const response = await fetch(`${getBaseUrl()}/clients/${clientId}/pause`, {
                method: 'POST'
            })
            if (!response.ok) {
                throw new Error(`Failed to pause client: ${response.statusText}`)
            }
            return await response.json()
        } catch (error) {
            console.error('Error pausing client:', error)
            throw error
        }
    }

    async function resumeClient(clientId) {
        try {
            const response = await fetch(`${getBaseUrl()}/clients/${clientId}/resume`, {
                method: 'POST'
            })
            if (!response.ok) {
                throw new Error(`Failed to resume client: ${response.statusText}`)
            }
            return await response.json()
        } catch (error) {
            console.error('Error resuming client:', error)
            throw error
        }
    }

    async function disconnectClient(clientId) {
        try {
            const response = await fetch(`${getBaseUrl()}/clients/${clientId}/disconnect`, {
                method: 'POST'
            })
            if (!response.ok) {
                throw new Error(`Failed to disconnect client: ${response.statusText}`)
            }
            return await response.json()
        } catch (error) {
            console.error('Error disconnecting client:', error)
            throw error
        }
    }

    // Fetch initial data from REST API
    async function fetchInitialData() {
        try {
            const response = await fetch('/statuses')
            if (response.ok) {
                const data = await response.json()

                // Clear existing clients and populate with fresh data
                clients.value.clear()

                // Update Redis status
                if (data.redis_status) {
                    redisStatus.value = data.redis_status
                }

                // Extract clients from the statuses response
                if (data.clients) {
                    Object.entries(data.clients).forEach(([clientId, clientData]) => {
                        updateClientData(clientId, clientData, false) // Initial load, not real-time
                    })
                }

                console.log(`Loaded ${Object.keys(data.clients || {}).length} clients from server`)
            }
        } catch (error) {
            console.error('Failed to fetch initial client data:', error)
        }
    }

    // Periodic data refresh to keep data synchronized
    async function startPeriodicRefresh() {
        const refreshInterval = 10000 // 10 seconds

        setInterval(async () => {
            if (connectionStatus.value === 'connected') {
                try {
                    const response = await fetch('/statuses')

                    if (response.ok) {
                        const data = await response.json()

                        // Update Redis status
                        if (data.redis_status) {
                            redisStatus.value = data.redis_status
                        }

                        // Update clients data (periodic refresh, not real-time)
                        if (data.clients) {
                            Object.entries(data.clients).forEach(([clientId, clientData]) => {
                                updateClientData(clientId, clientData, false) // Periodic refresh, not real-time
                            })
                        }
                    }

                    lastUpdate.value = new Date().toISOString()
                } catch (error) {
                    console.error('Failed to refresh data:', error)
                }
            }
        }, refreshInterval)
    }

    // Check if server is running
    async function checkServerHealth() {
        try {
            const response = await fetch('/statuses')
            return response.ok
        } catch (error) {
            console.error('Server health check failed:', error)
            return false
        }
    }

    // Initialize the store
    async function initialize() {
        console.log('Initializing WebSocket store...')

        // Check if server is running first
        const serverHealthy = await checkServerHealth()
        if (!serverHealthy) {
            console.error('❌ Server is not reachable')
            console.error('Please make sure the backend server is started on http://localhost:8000')
            connectionStatus.value = 'error'
            return
        }

        console.log('✅ Server is reachable, proceeding with initialization')
        await fetchInitialData()
        startPeriodicRefresh()
        connect()
    }

    return {
        // State
        connectionStatus,
        clients: clientsList,
        redisStatus,
        lastUpdate,
        frontendClientId,
        connectedClientsCount,
        isConnected,

        // Actions
        connect,
        disconnect,
        sendMessage,
        pauseClient,
        resumeClient,
        disconnectClient,
        fetchInitialData,
        updateClientData,
        initialize
    }
}) 
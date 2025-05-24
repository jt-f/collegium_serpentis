import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useWebSocketStore } from '../websocket.js'

// Mock WebSocket
global.WebSocket = vi.fn()
global.fetch = vi.fn()

describe('WebSocket Store', () => {
    let store
    let mockWebSocket

    beforeEach(() => {
        setActivePinia(createPinia())
        store = useWebSocketStore()

        // Create a mock WebSocket instance with proper event handlers
        mockWebSocket = {
            send: vi.fn(),
            close: vi.fn(),
            readyState: WebSocket.OPEN,
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
            onopen: null,
            onmessage: null,
            onclose: null,
            onerror: null
        }

        // Mock WebSocket constructor
        global.WebSocket.mockImplementation(() => mockWebSocket)

        // Reset fetch mock
        global.fetch.mockReset()
    })

    afterEach(() => {
        vi.clearAllMocks()
    })

    describe('initial state', () => {
        it('should have correct initial state', () => {
            expect(store.connectionStatus).toBe('disconnected')
            expect(store.clients).toEqual([])
            expect(store.redisStatus).toBe('unknown')
            expect(store.connectedClientsCount).toBe(0)
            expect(store.isConnected).toBe(false)
            expect(store.frontendClientId).toMatch(/^fe-[a-z0-9]{7}$/)
        })
    })

    describe('WebSocket connection', () => {
        it('should connect to WebSocket', () => {
            // Mock location for WebSocket URL
            Object.defineProperty(window, 'location', {
                value: {
                    protocol: 'http:',
                    host: 'localhost:3000'
                },
                writable: true
            })

            store.connect()

            expect(global.WebSocket).toHaveBeenCalledWith('ws://localhost:3000/ws')
            expect(store.connectionStatus).toBe('connecting')
        })

        it('should handle WebSocket open event', () => {
            store.connect()

            // Simulate onopen event by calling the assigned handler
            if (mockWebSocket.onopen) {
                mockWebSocket.onopen()
            }

            expect(store.connectionStatus).toBe('connected')
            expect(mockWebSocket.send).toHaveBeenCalledWith(
                JSON.stringify({
                    client_id: store.frontendClientId,
                    status: {
                        client_type: 'frontend',
                        connected_at: expect.any(String)
                    }
                })
            )
        })

        it('should handle WebSocket message', () => {
            store.connect()

            const mockMessage = {
                data: JSON.stringify({
                    result: 'message_processed',
                    client_id: 'test-client',
                    redis_status: 'connected'
                })
            }

            // Simulate onmessage event by calling the assigned handler
            if (mockWebSocket.onmessage) {
                mockWebSocket.onmessage(mockMessage)
            }

            expect(store.redisStatus).toBe('connected')
            expect(store.lastUpdate).toBeTruthy()
        })

        it('should handle WebSocket close with reconnection', () => {
            vi.useFakeTimers()
            store.connect()

            // Simulate onclose event by calling the assigned handler
            if (mockWebSocket.onclose) {
                mockWebSocket.onclose({ code: 1000, reason: 'Normal closure' })
            }

            expect(store.connectionStatus).toBe('disconnected')

            // Should attempt to reconnect
            vi.advanceTimersByTime(1000)
            expect(global.WebSocket).toHaveBeenCalledTimes(2)

            vi.useRealTimers()
        })

        it('should stop reconnection after max attempts', () => {
            vi.useFakeTimers()
            store.connect()

            // Simulate multiple disconnections
            for (let i = 0; i < 6; i++) {
                if (mockWebSocket.onclose) {
                    mockWebSocket.onclose({ code: 1000, reason: 'Test closure' })
                }
                vi.advanceTimersByTime(Math.pow(2, i) * 1000)
            }

            expect(store.connectionStatus).toBe('error')
            vi.useRealTimers()
        })
    })

    describe('client data management', () => {
        it('should update client data correctly', () => {
            const clientData = {
                connected: 'true',
                client_state: 'running',
                cpu_usage: '45%',
                memory_usage: '60%'
            }

            store.updateClientData('test-client', clientData)

            const client = store.clients.find(c => c.id === 'test-client')
            expect(client).toBeTruthy()
            expect(client.status).toBe('Connected')
            expect(client.details).toContain('CPU: 45%')
            expect(client.canPause).toBe(true)
            expect(client.canDisconnect).toBe(true)
        })

        it('should handle disconnected client data', () => {
            const clientData = {
                connected: 'false',
                disconnect_time: '2023-10-26T10:00:00Z'
            }

            store.updateClientData('test-client', clientData)

            const client = store.clients.find(c => c.id === 'test-client')
            expect(client.status).toBe('Disconnected')
            expect(client.canPause).toBe(false)
            expect(client.canDisconnect).toBe(false)
        })

        it('should handle paused client data', () => {
            const clientData = {
                connected: 'true',
                client_state: 'paused'
            }

            store.updateClientData('test-client', clientData)

            const client = store.clients.find(c => c.id === 'test-client')
            expect(client.status).toBe('Paused')
            expect(client.canPause).toBe(false)
            expect(client.canResume).toBe(true)
        })
    })

    describe('client actions', () => {
        beforeEach(() => {
            global.fetch.mockResolvedValue({
                ok: true,
                json: () => Promise.resolve({ message: 'success' })
            })
        })

        it('should pause client', async () => {
            const result = await store.pauseClient('test-client')

            expect(global.fetch).toHaveBeenCalledWith('/clients/test-client/pause', {
                method: 'POST'
            })
            expect(result).toEqual({ message: 'success' })
        })

        it('should resume client', async () => {
            const result = await store.resumeClient('test-client')

            expect(global.fetch).toHaveBeenCalledWith('/clients/test-client/resume', {
                method: 'POST'
            })
            expect(result).toEqual({ message: 'success' })
        })

        it('should disconnect client', async () => {
            const result = await store.disconnectClient('test-client')

            expect(global.fetch).toHaveBeenCalledWith('/clients/test-client/disconnect', {
                method: 'POST'
            })
            expect(result).toEqual({ message: 'success' })
        })

        it('should handle fetch errors', async () => {
            global.fetch.mockResolvedValue({
                ok: false,
                statusText: 'Not Found'
            })

            await expect(store.pauseClient('test-client')).rejects.toThrow('Failed to pause client: Not Found')
        })
    })

    describe('data fetching', () => {
        it('should fetch initial data successfully', async () => {
            const mockClientData = {
                clients: {
                    'client1': { connected: 'true', client_state: 'running' },
                    'client2': { connected: 'false', disconnect_time: '2023-10-26T10:00:00Z' }
                }
            }

            global.fetch.mockResolvedValue({
                ok: true,
                json: () => Promise.resolve(mockClientData)
            })

            await store.fetchInitialData()

            expect(store.clients).toHaveLength(2)
            expect(store.clients[0].id).toBe('client1')
            expect(store.clients[1].id).toBe('client2')
        })

        it('should handle fetch errors gracefully', async () => {
            global.fetch.mockRejectedValue(new Error('Network error'))

            // Should not throw
            await expect(store.fetchInitialData()).resolves.toBeUndefined()
        })
    })

    describe('utility functions', () => {
        it('should format display status correctly', () => {
            expect(store.updateClientData('test1', { connected: 'true' }))
            expect(store.updateClientData('test2', { connected: 'false' }))
            expect(store.updateClientData('test3', { connected: 'true', client_state: 'paused' }))

            const clients = store.clients
            expect(clients.find(c => c.id === 'test1').status).toBe('Connected')
            expect(clients.find(c => c.id === 'test2').status).toBe('Disconnected')
            expect(clients.find(c => c.id === 'test3').status).toBe('Paused')
        })

        it('should calculate connected clients count', () => {
            store.updateClientData('client1', { connected: 'true' })
            store.updateClientData('client2', { connected: 'false' })
            store.updateClientData('client3', { connected: 'true' })

            expect(store.connectedClientsCount).toBe(2)
        })
    })

    describe('timestamp management', () => {
        beforeEach(() => {
            vi.useFakeTimers()
            vi.setSystemTime(new Date('2023-10-26T10:00:00Z'))
        })

        afterEach(() => {
            vi.useRealTimers()
        })

        it('should update lastUpdate for real-time WebSocket updates', () => {
            const clientData = {
                connected: 'true',
                timestamp: '2023-10-26T09:30:00Z'
            }

            store.updateClientData('test-client', clientData, true) // Real-time update

            const client = store.clients.find(c => c.id === 'test-client')
            expect(client.lastUpdate).toBe('2023-10-26T09:30:00Z')
        })

        it('should preserve lastUpdate for periodic updates with unchanged data', () => {
            // First, add a client with real-time data
            const initialData = {
                connected: 'true',
                client_state: 'running',
                timestamp: '2023-10-26T09:30:00Z'
            }
            store.updateClientData('test-client', initialData, true)

            // Now simulate a periodic update with the same data
            vi.setSystemTime(new Date('2023-10-26T10:05:00Z'))
            store.updateClientData('test-client', initialData, false) // Periodic update

            const client = store.clients.find(c => c.id === 'test-client')
            // Should preserve the original timestamp, not update to current time
            expect(client.lastUpdate).toBe('2023-10-26T09:30:00Z')
        })

        it('should update lastUpdate for periodic updates when data changes', () => {
            // First, add a client
            const initialData = {
                connected: 'true',
                client_state: 'running',
                timestamp: '2023-10-26T09:30:00Z'
            }
            store.updateClientData('test-client', initialData, true)

            // Now simulate a periodic update with changed data
            vi.setSystemTime(new Date('2023-10-26T10:05:00Z'))
            const changedData = {
                connected: 'true',
                client_state: 'paused', // Changed from 'running'
                timestamp: '2023-10-26T10:00:00Z'
            }
            store.updateClientData('test-client', changedData, false)

            const client = store.clients.find(c => c.id === 'test-client')
            // Should use the new timestamp since data changed
            expect(client.lastUpdate).toBe('2023-10-26T10:00:00Z')
        })

        it('should handle first-time client registration', () => {
            const clientData = {
                connected: 'true',
                client_state: 'running'
            }

            store.updateClientData('new-client', clientData, false)

            const client = store.clients.find(c => c.id === 'new-client')
            // Should get current time since it's a new client
            expect(client.lastUpdate).toBe('2023-10-26T10:00:00.000Z')
        })

        it('should detect data changes correctly', () => {
            const initialData = {
                connected: 'true',
                client_state: 'running',
                cpu_usage: '45%'
            }
            store.updateClientData('test-client', initialData, true)

            // Test various types of changes
            const changedConnected = { ...initialData, connected: 'false' }
            const changedState = { ...initialData, client_state: 'paused' }
            const changedCPU = { ...initialData, cpu_usage: '50%' }
            const unchangedData = { ...initialData }

            store.updateClientData('test-client', changedConnected, false)
            expect(store.clients.find(c => c.id === 'test-client').lastUpdate).not.toBe(initialData.timestamp)

            // Reset for next test
            store.updateClientData('test-client', initialData, true)

            store.updateClientData('test-client', changedState, false)
            expect(store.clients.find(c => c.id === 'test-client').lastUpdate).not.toBe(initialData.timestamp)

            // Reset for next test
            store.updateClientData('test-client', initialData, true)

            store.updateClientData('test-client', changedCPU, false)
            expect(store.clients.find(c => c.id === 'test-client').lastUpdate).not.toBe(initialData.timestamp)
        })

        it('should format lastSeen for paused clients correctly', () => {
            const pausedData = {
                connected: 'true',
                client_state: 'paused',
                timestamp: '2023-10-26T09:30:00Z'
            }

            store.updateClientData('paused-client', pausedData, true)

            const client = store.clients.find(c => c.id === 'paused-client')
            expect(client.lastSeen).toContain('Paused since:')
            expect(client.lastSeen).toContain('2023') // Should include the year
        })

        it('should format lastSeen with relative time for recent activity', () => {
            vi.setSystemTime(new Date('2023-10-26T10:00:00Z'))

            const recentData = {
                connected: 'true',
                client_state: 'running',
                timestamp: '2023-10-26T09:59:30Z' // 30 seconds ago
            }

            store.updateClientData('recent-client', recentData, true)

            const client = store.clients.find(c => c.id === 'recent-client')
            expect(client.lastSeen).toBe('Active now')
        })

        it('should format lastSeen with minutes for older activity', () => {
            vi.setSystemTime(new Date('2023-10-26T10:00:00Z'))

            const olderData = {
                connected: 'true',
                client_state: 'running',
                timestamp: '2023-10-26T09:55:00Z' // 5 minutes ago
            }

            store.updateClientData('older-client', olderData, true)

            const client = store.clients.find(c => c.id === 'older-client')
            expect(client.lastSeen).toBe('Last seen: 5 minutes ago')
        })

        it('should format lastSeen with full timestamp for very old activity', () => {
            vi.setSystemTime(new Date('2023-10-26T10:00:00Z'))

            const veryOldData = {
                connected: 'true',
                client_state: 'running',
                timestamp: '2023-10-26T08:00:00Z' // 2 hours ago
            }

            store.updateClientData('very-old-client', veryOldData, true)

            const client = store.clients.find(c => c.id === 'very-old-client')
            expect(client.lastSeen).toContain('Last seen:')
            expect(client.lastSeen).toContain('2023') // Should include the year
        })
    })

    describe('WebSocket message handling with timestamps', () => {
        it('should handle real-time client status updates with correct timestamp flags', () => {
            store.connect()

            const mockMessage = {
                data: JSON.stringify({
                    client_id: 'real-time-client',
                    status: {
                        connected: 'true',
                        client_state: 'running',
                        timestamp: '2023-10-26T09:30:00Z'
                    }
                })
            }

            // Simulate onmessage event by calling the assigned handler
            if (mockWebSocket.onmessage) {
                mockWebSocket.onmessage(mockMessage)
            }

            const client = store.clients.find(c => c.id === 'real-time-client')
            expect(client).toBeTruthy()
            expect(client.lastUpdate).toBe('2023-10-26T09:30:00Z')
        })
    })
}) 
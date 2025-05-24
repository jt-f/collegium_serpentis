import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createTestingPinia } from '@pinia/testing'
import ClientsTable from '../ClientsTable.vue'
import { useWebSocketStore } from '../../stores/websocket.js'

// Mock the child component
vi.mock('../ClientTableRow.vue', () => ({
    default: {
        name: 'ClientTableRow',
        props: ['clientData'],
        emits: ['action'],
        template: '<tr><td>{{ clientData.id }}</td></tr>'
    }
}))

describe('ClientsTable', () => {
    let wrapper
    let mockStore

    beforeEach(() => {
        const pinia = createTestingPinia({
            createSpy: vi.fn
        })

        wrapper = mount(ClientsTable, {
            global: {
                plugins: [pinia]
            }
        })

        mockStore = useWebSocketStore()
    })

    it('should render with no clients', async () => {
        mockStore.clients = []
        mockStore.connectedClientsCount = 0
        mockStore.isConnected = true
        mockStore.connectionStatus = 'connected'

        await wrapper.vm.$nextTick()

        expect(wrapper.find('h2').text()).toBe('Connected Clients (0)')
        expect(wrapper.find('.no-clients-message').text()).toContain('No clients connected or reporting')
    })

    it('should show connection warning when disconnected', () => {
        mockStore.isConnected = false
        mockStore.connectionStatus = 'disconnected'

        expect(wrapper.find('.connection-warning').exists()).toBe(true)
        expect(wrapper.find('.connection-warning').text()).toContain('WebSocket connection: disconnected')
    })

    it('should render clients list', async () => {
        const mockClients = [
            {
                id: 'client1',
                status: 'Connected',
                lastSeen: 'Last seen: 10:35:02',
                details: 'CPU: 45%',
                canPause: true,
                canResume: false,
                canDisconnect: true
            },
            {
                id: 'client2',
                status: 'Disconnected',
                lastSeen: 'Disconnected: 2023-10-26',
                details: 'N/A',
                canPause: false,
                canResume: false,
                canDisconnect: false
            }
        ]

        mockStore.clients = mockClients
        mockStore.connectedClientsCount = 1
        mockStore.isConnected = true

        await wrapper.vm.$nextTick()

        expect(wrapper.find('h2').text()).toBe('Connected Clients (1)')
        expect(wrapper.findAll('tbody tr')).toHaveLength(2)
    })

    it('should handle client actions', async () => {
        mockStore.pauseClient = vi.fn().mockResolvedValue({ message: 'success' })
        mockStore.resumeClient = vi.fn().mockResolvedValue({ message: 'success' })
        mockStore.disconnectClient = vi.fn().mockResolvedValue({ message: 'success' })

        // Test pause action
        await wrapper.vm.handleClientAction({ action: 'pause', clientId: 'test-client' })
        expect(mockStore.pauseClient).toHaveBeenCalledWith('test-client')

        // Test resume action
        await wrapper.vm.handleClientAction({ action: 'resume', clientId: 'test-client' })
        expect(mockStore.resumeClient).toHaveBeenCalledWith('test-client')

        // Test disconnect action
        await wrapper.vm.handleClientAction({ action: 'disconnect', clientId: 'test-client' })
        expect(mockStore.disconnectClient).toHaveBeenCalledWith('test-client')
    })

    it('should handle action errors gracefully', async () => {
        mockStore.pauseClient = vi.fn().mockRejectedValue(new Error('Network error'))

        const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => { })

        await wrapper.vm.handleClientAction({ action: 'pause', clientId: 'test-client' })

        expect(consoleSpy).toHaveBeenCalledWith('Failed to pause client test-client:', expect.any(Error))

        consoleSpy.mockRestore()
    })

    it('should handle unknown actions', async () => {
        const consoleSpy = vi.spyOn(console, 'warn').mockImplementation(() => { })

        await wrapper.vm.handleClientAction({ action: 'unknown', clientId: 'test-client' })

        expect(consoleSpy).toHaveBeenCalledWith('Unknown action: unknown')

        consoleSpy.mockRestore()
    })

    it('should have proper styling classes', () => {
        expect(wrapper.find('.status-block').exists()).toBe(true)
        expect(wrapper.find('.clients-table').exists()).toBe(true)
        expect(wrapper.find('table').exists()).toBe(true)
    })
}) 
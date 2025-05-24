import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createTestingPinia } from '@pinia/testing'
import ServerConnectionMonitor from '../ServerConnectionMonitor.vue'
import { useWebSocketStore } from '../../stores/websocket.js'

describe('ServerConnectionMonitor', () => {
    let wrapper
    let mockStore

    beforeEach(() => {
        const pinia = createTestingPinia({
            createSpy: vi.fn
        })

        wrapper = mount(ServerConnectionMonitor, {
            global: {
                plugins: [pinia]
            }
        })

        mockStore = useWebSocketStore()
    })

    describe('component rendering', () => {
        it('should render the component with title', () => {
            expect(wrapper.find('h3').text()).toBe('Server Connection')
            expect(wrapper.find('.status-block').exists()).toBe(true)
            expect(wrapper.find('.server-connection').exists()).toBe(true)
        })

        it('should have proper styling classes', () => {
            expect(wrapper.find('.status-block').exists()).toBe(true)
            expect(wrapper.find('.connection-details').exists()).toBe(true)
            expect(wrapper.find('.connection-status').exists()).toBe(true)
        })
    })

    describe('connection status display', () => {
        it('should display connected status correctly', async () => {
            mockStore.connectionStatus = 'connected'
            mockStore.isConnected = true
            mockStore.lastUpdate = '2023-10-26T10:30:00Z'

            await wrapper.vm.$nextTick()

            expect(wrapper.find('.status-text').text()).toBe('Connected')
            expect(wrapper.find('.status-connected').exists()).toBe(true)
            expect(wrapper.find('.indicator-connected').exists()).toBe(true)
            expect(wrapper.find('.last-update').exists()).toBe(true)
            expect(wrapper.find('.last-update').text()).toContain('Last message:')
        })

        it('should display connecting status correctly', async () => {
            mockStore.connectionStatus = 'connecting'
            mockStore.isConnected = false

            await wrapper.vm.$nextTick()

            expect(wrapper.find('.status-text').text()).toBe('Connecting')
            expect(wrapper.find('.status-connecting').exists()).toBe(true)
            expect(wrapper.find('.indicator-connecting').exists()).toBe(true)
            expect(wrapper.find('.connecting-info').exists()).toBe(true)
            expect(wrapper.find('.connecting-info').text()).toContain('Establishing connection')
        })

        it('should display disconnected status correctly', async () => {
            mockStore.connectionStatus = 'disconnected'
            mockStore.isConnected = false

            await wrapper.vm.$nextTick()

            expect(wrapper.find('.status-text').text()).toBe('Disconnected')
            expect(wrapper.find('.status-disconnected').exists()).toBe(true)
            expect(wrapper.find('.indicator-disconnected').exists()).toBe(true)
            expect(wrapper.find('.last-update').exists()).toBe(false)
        })

        it('should display error status correctly', async () => {
            mockStore.connectionStatus = 'error'
            mockStore.isConnected = false

            await wrapper.vm.$nextTick()

            expect(wrapper.find('.status-text').text()).toBe('Connection Failed')
            expect(wrapper.find('.status-error').exists()).toBe(true)
            expect(wrapper.find('.indicator-error').exists()).toBe(true)
            expect(wrapper.find('.error-details').exists()).toBe(true)
            expect(wrapper.find('.error-message').text()).toContain('Cannot reach backend server')
            expect(wrapper.find('.error-suggestion').text()).toContain('localhost:8000')
        })

        it('should display unknown status correctly', async () => {
            mockStore.connectionStatus = 'unknown'
            mockStore.isConnected = false

            await wrapper.vm.$nextTick()

            expect(wrapper.find('.status-text').text()).toBe('Unknown')
            expect(wrapper.find('.status-unknown').exists()).toBe(true)
            expect(wrapper.find('.indicator-unknown').exists()).toBe(true)
        })
    })

    describe('reconnection features', () => {
        it('should show reconnection success message', async () => {
            mockStore.connectionStatus = 'connected'
            mockStore.isConnected = true
            mockStore.reconnectAttempts = 3

            await wrapper.vm.$nextTick()

            expect(wrapper.find('.reconnect-info').exists()).toBe(true)
            expect(wrapper.find('.reconnect-info').text()).toContain('Reconnected after 3 attempts')
            expect(wrapper.find('.success-indicator').exists()).toBe(true)
        })

        it('should handle single reconnection attempt correctly', async () => {
            mockStore.connectionStatus = 'connected'
            mockStore.isConnected = true
            mockStore.reconnectAttempts = 1

            await wrapper.vm.$nextTick()

            expect(wrapper.find('.reconnect-info').text()).toContain('Reconnected after 1 attempt')
            // Should not show plural "attempts"
            expect(wrapper.find('.reconnect-info').text()).not.toContain('attempts')
        })

        it('should not show reconnection info when no reconnection occurred', async () => {
            mockStore.connectionStatus = 'connected'
            mockStore.isConnected = true
            mockStore.reconnectAttempts = 0

            await wrapper.vm.$nextTick()

            expect(wrapper.find('.reconnect-info').exists()).toBe(false)
        })

        it('should not show reconnection info when disconnected', async () => {
            mockStore.connectionStatus = 'disconnected'
            mockStore.isConnected = false
            mockStore.reconnectAttempts = 2

            await wrapper.vm.$nextTick()

            expect(wrapper.find('.reconnect-info').exists()).toBe(false)
        })
    })

    describe('retry functionality', () => {
        it('should show retry button when in error state', async () => {
            mockStore.connectionStatus = 'error'
            mockStore.isConnected = false

            await wrapper.vm.$nextTick()

            const retryBtn = wrapper.find('.retry-btn')
            expect(retryBtn.exists()).toBe(true)
            expect(retryBtn.text()).toContain('Retry Connection')
            expect(retryBtn.attributes('disabled')).toBeUndefined()
        })

        it('should call store initialize when retry button is clicked', async () => {
            mockStore.connectionStatus = 'error'
            mockStore.isConnected = false
            mockStore.initialize = vi.fn().mockResolvedValue()

            await wrapper.vm.$nextTick()

            const retryBtn = wrapper.find('.retry-btn')
            await retryBtn.trigger('click')

            expect(mockStore.initialize).toHaveBeenCalledOnce()
        })

        it('should show retrying state and disable button during retry', async () => {
            mockStore.connectionStatus = 'error'
            mockStore.isConnected = false
            mockStore.initialize = vi.fn().mockImplementation(() =>
                new Promise(resolve => setTimeout(resolve, 100))
            )

            await wrapper.vm.$nextTick()

            const retryBtn = wrapper.find('.retry-btn')
            await retryBtn.trigger('click')

            // Check that button is disabled and shows retrying text
            await wrapper.vm.$nextTick()
            expect(retryBtn.text()).toContain('Retrying...')
            expect(retryBtn.attributes('disabled')).toBeDefined()
        })

        it('should handle retry errors gracefully', async () => {
            mockStore.connectionStatus = 'error'
            mockStore.isConnected = false
            mockStore.initialize = vi.fn().mockRejectedValue(new Error('Network error'))

            const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => { })

            await wrapper.vm.$nextTick()

            const retryBtn = wrapper.find('.retry-btn')
            await retryBtn.trigger('click')

            // Wait for the retry logic to complete
            await new Promise(resolve => setTimeout(resolve, 50))

            expect(consoleSpy).toHaveBeenCalledWith('Failed to retry connection:', expect.any(Error))

            consoleSpy.mockRestore()
        })

        it('should not show retry button when not in error state', async () => {
            mockStore.connectionStatus = 'connected'
            mockStore.isConnected = true

            await wrapper.vm.$nextTick()

            expect(wrapper.find('.retry-btn').exists()).toBe(false)
        })
    })

    describe('time formatting', () => {
        it('should format time correctly', () => {
            const testTime = '2023-10-26T14:30:45Z'
            const formatted = wrapper.vm.formatTime(testTime)

            // Should return a localized time string
            expect(formatted).toMatch(/\d{1,2}:\d{2}:\d{2}/)
        })

        it('should handle empty time string', () => {
            expect(wrapper.vm.formatTime('')).toBe('')
            expect(wrapper.vm.formatTime(null)).toBe('')
            expect(wrapper.vm.formatTime(undefined)).toBe('')
        })

        it('should display formatted time in last update', async () => {
            mockStore.connectionStatus = 'connected'
            mockStore.isConnected = true
            mockStore.lastUpdate = '2023-10-26T14:30:45Z'

            await wrapper.vm.$nextTick()

            const lastUpdate = wrapper.find('.last-update')
            expect(lastUpdate.exists()).toBe(true)
            expect(lastUpdate.text()).toContain('Last message:')
            expect(lastUpdate.text()).toMatch(/\d{1,2}:\d{2}:\d{2}/)
        })
    })

    describe('computed properties', () => {
        it('should compute status text correctly for all states', () => {
            const testCases = [
                { status: 'connected', expected: 'Connected' },
                { status: 'connecting', expected: 'Connecting' },
                { status: 'disconnected', expected: 'Disconnected' },
                { status: 'error', expected: 'Connection Failed' },
                { status: 'unknown', expected: 'Unknown' },
                { status: 'invalid', expected: 'Unknown' }
            ]

            testCases.forEach(({ status, expected }) => {
                mockStore.connectionStatus = status
                expect(wrapper.vm.statusText).toBe(expected)
            })
        })

        it('should compute status classes correctly', () => {
            const testCases = [
                { status: 'connected', expected: 'status-connected' },
                { status: 'connecting', expected: 'status-connecting' },
                { status: 'disconnected', expected: 'status-disconnected' },
                { status: 'error', expected: 'status-error' },
                { status: 'unknown', expected: 'status-unknown' }
            ]

            testCases.forEach(({ status, expected }) => {
                mockStore.connectionStatus = status
                expect(wrapper.vm.statusClass).toBe(expected)
            })
        })

        it('should compute indicator classes correctly', () => {
            const testCases = [
                { status: 'connected', expected: 'indicator-connected' },
                { status: 'connecting', expected: 'indicator-connecting' },
                { status: 'disconnected', expected: 'indicator-disconnected' },
                { status: 'error', expected: 'indicator-error' },
                { status: 'unknown', expected: 'indicator-unknown' }
            ]

            testCases.forEach(({ status, expected }) => {
                mockStore.connectionStatus = status
                expect(wrapper.vm.statusIndicatorClass).toBe(expected)
            })
        })
    })

    describe('conditional rendering', () => {
        it('should only show last update when connected and has lastUpdate', async () => {
            // Case 1: Connected with lastUpdate
            mockStore.connectionStatus = 'connected'
            mockStore.isConnected = true
            mockStore.lastUpdate = '2023-10-26T10:30:00Z'

            await wrapper.vm.$nextTick()
            expect(wrapper.find('.last-update').exists()).toBe(true)

            // Case 2: Connected without lastUpdate
            mockStore.lastUpdate = null
            await wrapper.vm.$nextTick()
            expect(wrapper.find('.last-update').exists()).toBe(false)

            // Case 3: Not connected with lastUpdate
            mockStore.isConnected = false
            mockStore.lastUpdate = '2023-10-26T10:30:00Z'
            await wrapper.vm.$nextTick()
            expect(wrapper.find('.last-update').exists()).toBe(false)
        })

        it('should only show connecting info when status is connecting', async () => {
            mockStore.connectionStatus = 'connecting'
            await wrapper.vm.$nextTick()
            expect(wrapper.find('.connecting-info').exists()).toBe(true)

            mockStore.connectionStatus = 'connected'
            await wrapper.vm.$nextTick()
            expect(wrapper.find('.connecting-info').exists()).toBe(false)
        })

        it('should only show error details when status is error', async () => {
            mockStore.connectionStatus = 'error'
            await wrapper.vm.$nextTick()
            expect(wrapper.find('.error-details').exists()).toBe(true)

            mockStore.connectionStatus = 'connected'
            await wrapper.vm.$nextTick()
            expect(wrapper.find('.error-details').exists()).toBe(false)
        })
    })
}) 
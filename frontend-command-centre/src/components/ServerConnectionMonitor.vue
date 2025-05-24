<template>
  <div class="status-block server-connection">
    <h3>Server Connection</h3>
    <div class="connection-details">
      <div class="connection-status">
        <span class="status-indicator" :class="statusIndicatorClass"></span>
        <span class="status-text" :class="statusClass">{{ statusText }}</span>
      </div>
      <div v-if="isConnected && reconnectAttempts > 0" class="reconnect-info">
        <span class="success-indicator">✓</span>
        Reconnected after {{ reconnectAttempts }} attempt{{ reconnectAttempts === 1 ? '' : 's' }}
      </div>
      <div v-if="connectionStatus === 'error'" class="error-details">
        <p class="error-message">Cannot reach backend server</p>
        <p class="error-suggestion">Please ensure the server is running on <code>localhost:8000</code></p>
        <button @click="retryConnection" class="retry-btn" :disabled="isRetrying">
          <span v-if="isRetrying">🔄 Retrying...</span>
          <span v-else>🔄 Retry Connection</span>
        </button>
      </div>
      <div v-if="connectionStatus === 'connecting'" class="connecting-info">
        <span class="connecting-spinner">⏳</span>
        <span>Establishing connection...</span>
      </div>
    </div>
    <div v-if="lastUpdate && isConnected" class="last-update">
      Last message: {{ formatTime(lastUpdate) }}
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useWebSocketStore } from '../stores/websocket.js'

const wsStore = useWebSocketStore()
const isRetrying = ref(false)

// Computed properties from the store
const connectionStatus = computed(() => wsStore.connectionStatus)
const isConnected = computed(() => wsStore.isConnected)
const lastUpdate = computed(() => wsStore.lastUpdate)
const reconnectAttempts = computed(() => wsStore.reconnectAttempts || 0)

const statusText = computed(() => {
  switch (connectionStatus.value) {
    case 'connected':
      return 'Connected'
    case 'connecting':
      return 'Connecting'
    case 'disconnected':
      return 'Disconnected'
    case 'error':
      return 'Connection Failed'
    default:
      return 'Unknown'
  }
})

const statusClass = computed(() => {
  switch (connectionStatus.value) {
    case 'connected':
      return 'status-connected'
    case 'connecting':
      return 'status-connecting'
    case 'disconnected':
      return 'status-disconnected'
    case 'error':
      return 'status-error'
    default:
      return 'status-unknown'
  }
})

const statusIndicatorClass = computed(() => {
  switch (connectionStatus.value) {
    case 'connected':
      return 'indicator-connected'
    case 'connecting':
      return 'indicator-connecting'
    case 'disconnected':
      return 'indicator-disconnected'
    case 'error':
      return 'indicator-error'
    default:
      return 'indicator-unknown'
  }
})

async function retryConnection() {
  isRetrying.value = true
  try {
    await wsStore.initialize()
  } catch (error) {
    console.error('Failed to retry connection:', error)
  } finally {
    // Add a small delay to show the retrying state
    setTimeout(() => {
      isRetrying.value = false
    }, 1000)
  }
}

function formatTime(isoString) {
  if (!isoString) return ''
  return new Date(isoString).toLocaleTimeString()
}
</script>

<style scoped>
.status-block {
  padding: 1rem 1.5rem;
  border: 1px solid #495057; /* Subtle Gray */
  border-radius: 4px;
  background-color: #343A40; /* Dark Cool Gray */
  margin-top: 1rem;
}

.status-block h3 {
  margin-top: 0;
  margin-bottom: 0.5rem;
  font-size: 1.1rem;
  font-weight: 600;
  color: #F8F9FA;
  border-bottom: 1px solid #495057;
  padding-bottom: 0.5rem;
}

.connection-details {
  margin-bottom: 1rem;
}

.connection-status {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}

.status-indicator {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  display: inline-block;
  position: relative;
}

.indicator-connected {
  background-color: #10B981; /* Desaturated Green */
  box-shadow: 0 0 8px rgba(16, 185, 129, 0.4);
}

.indicator-connecting {
  background-color: #F59E0B; /* Desaturated Amber */
  animation: pulse 1.5s ease-in-out infinite;
}

.indicator-disconnected {
  background-color: #6B7280; /* Medium Gray */
}

.indicator-error {
  background-color: #EF4444; /* Desaturated Red */
  animation: pulse 2s ease-in-out infinite;
}

.indicator-unknown {
  background-color: #D97706; /* Desaturated Amber */
}

@keyframes pulse {
  0%, 100% {
    opacity: 1;
    transform: scale(1);
  }
  50% {
    opacity: 0.6;
    transform: scale(1.1);
  }
}

.status-text {
  font-weight: 600;
  font-size: 0.95rem;
}

.status-connected {
  color: #10B981; /* Desaturated Green */
}

.status-connecting {
  color: #F59E0B; /* Desaturated Amber */
}

.status-disconnected {
  color: #6B7280; /* Medium Gray */
}

.status-error {
  color: #EF4444; /* Desaturated Red */
}

.status-unknown {
  color: #D97706; /* Desaturated Amber */
}

.reconnect-info {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.85rem;
  color: #10B981; /* Desaturated Green */
  background-color: rgba(16, 185, 129, 0.1);
  padding: 0.4rem 0.6rem;
  border-radius: 3px;
  margin-top: 0.5rem;
}

.success-indicator {
  font-weight: bold;
  color: #10B981;
}

.error-details {
  background-color: rgba(239, 68, 68, 0.1);
  border: 1px solid rgba(239, 68, 68, 0.2);
  border-radius: 4px;
  padding: 0.75rem;
  margin-top: 0.5rem;
}

.error-message {
  color: #EF4444;
  font-weight: 600;
  margin: 0 0 0.5rem 0;
  font-size: 0.9rem;
}

.error-suggestion {
  color: #F8F9FA;
  margin: 0 0 0.75rem 0;
  font-size: 0.85rem;
}

.error-suggestion code {
  background-color: #495057;
  padding: 0.2rem 0.4rem;
  border-radius: 2px;
  font-family: 'Courier New', monospace;
  font-size: 0.8rem;
}

.retry-btn {
  background-color: #EF4444;
  border: 1px solid #EF4444;
  color: #FEF2F2;
  padding: 0.4rem 0.8rem;
  border-radius: 3px;
  cursor: pointer;
  font-size: 0.8rem;
  font-weight: 500;
  transition: all 0.2s ease;
}

.retry-btn:hover:not(:disabled) {
  background-color: #DC2626;
  border-color: #DC2626;
}

.retry-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.connecting-info {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.85rem;
  color: #F59E0B;
  background-color: rgba(245, 158, 11, 0.1);
  padding: 0.4rem 0.6rem;
  border-radius: 3px;
  margin-top: 0.5rem;
}

.connecting-spinner {
  animation: spin 2s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.last-update {
  font-size: 0.8em;
  color: #6C757D; /* Medium Gray */
  margin-top: 0.5rem;
  padding-top: 0.5rem;
  border-top: 1px solid #495057;
}
</style> 
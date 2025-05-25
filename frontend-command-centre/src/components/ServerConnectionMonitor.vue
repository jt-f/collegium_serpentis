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
  padding: 1rem 1.5rem; /* 16px 24px */
  border: 1px solid var(--color-border); /* Olive Drab/Spy Green */
  border-radius: 4px;
  background-color: var(--color-background-soft); /* Slightly Lighter Background */
  margin-top: 1rem; /* Consistent with other blocks */
  position: relative; /* For pseudo-element positioning */
  overflow: hidden; /* To contain the pseudo-element if it bleeds */
}

/* "Classified Stamp" motif */
.status-block::after {
  content: '';
  position: absolute;
  top: 0;
  right: 0;
  width: 0;
  height: 0;
  border-left: 30px solid transparent; /* Adjust size as needed */
  border-top: 30px solid rgba(140, 58, 58, 0.6); /* --color-accent-red (#8C3A3A) with alpha */
}

.status-block h3 {
  margin-top: 0;
  margin-bottom: 0.5rem; /* 8px */
  font-family: 'Special Elite', cursive; /* Thematic font for titles */
  font-size: 1.3rem; /* Adjusted for Special Elite */
  font-weight: normal; /* Special Elite is often bold by default */
  color: var(--color-heading);
  padding-bottom: 0.25rem; /* 4px, subtle separation */
  border-bottom: 1px dashed var(--color-text-muted); /* Dashed line for a "field label" feel */
}

.connection-details {
  margin-bottom: 1rem;
}

.connection-status {
  display: flex;
  align-items: center;
  gap: 0.5rem; /* 8px */
  margin-bottom: 0.5rem; /* 8px */
}

.status-indicator {
  width: 10px; /* Slightly smaller */
  height: 10px;
  border-radius: 50%;
  display: inline-block;
  position: relative;
  /* Remove box-shadow from base, apply per state if needed */
}

.indicator-connected {
  background-color: var(--color-accent-green);
  animation: pulse-green 2s infinite;
}

.indicator-connecting {
  background-color: var(--color-accent-manila); /* Using Manila for yellow/amber */
  animation: pulse-yellow 1.2s infinite;
}

.indicator-disconnected {
  background-color: var(--color-text-muted);
}

.indicator-error {
  background-color: var(--color-accent-red);
  animation: pulse-red 2.5s infinite;
}

.indicator-unknown { /* Covers 'initial' state from store */
  background-color: var(--color-text-muted);
}

@keyframes pulse-green {
  0%, 100% { opacity: 1; box-shadow: 0 0 4px var(--color-accent-green); }
  50% { opacity: 0.7; box-shadow: 0 0 7px var(--color-accent-green); }
}

@keyframes pulse-yellow {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.6; transform: scale(1.1); }
}

@keyframes pulse-red {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.status-text {
  font-weight: 700; /* Bolder text for status */
  font-size: 0.95rem;
}

.status-connected {
  color: var(--color-accent-green);
}

.status-connecting {
  color: var(--color-accent-manila); /* Using Manila */
}

.status-disconnected {
  color: var(--color-text-muted);
}

.status-error {
  color: var(--color-accent-red);
}

.status-unknown {
  color: var(--color-text-muted);
}

.reconnect-info {
  display: flex;
  align-items: center;
  gap: 0.5rem; /* 8px */
  font-size: 0.85rem;
  color: var(--color-accent-green);
  background-color: rgba(85, 107, 47, 0.2); /* Accent Green with low alpha */
  padding: 0.4rem 0.6rem;
  border-radius: 3px;
  margin-top: 0.5rem; /* 8px */
}

.success-indicator {
  font-weight: bold;
  color: var(--color-accent-green);
}

.error-details {
  background-color: rgba(140, 58, 58, 0.15); /* Accent Red with low alpha */
  border: 1px solid rgba(140, 58, 58, 0.3);
  border-radius: 4px;
  padding: 0.75rem; /* 12px */
  margin-top: 0.5rem; /* 8px */
}

.error-message {
  color: var(--color-accent-red);
  font-weight: 700;
  margin: 0 0 0.5rem 0;
  font-size: 0.9rem;
}

.error-suggestion {
  color: var(--color-text); /* Primary text color */
  margin: 0 0 0.75rem 0;
  font-size: 0.85rem;
}

.error-suggestion code {
  background-color: var(--color-background); /* Base background for code */
  color: var(--color-text-muted);
  padding: 0.2rem 0.4rem;
  border-radius: 2px;
  font-family: 'Courier New', monospace; /* Keep monospace for code */
  font-size: 0.8rem;
  border: 1px solid var(--color-border);
}

.retry-btn {
  background-color: var(--color-accent-red);
  border: 1px solid var(--color-accent-red);
  color: var(--color-text-on-accent); /* White text on red */
  padding: 0.4rem 0.8rem;
  border-radius: 3px;
  cursor: pointer;
  font-size: 0.8rem;
  font-weight: 500;
  transition: background-color 0.2s ease, border-color 0.2s ease;
}

.retry-btn:hover:not(:disabled) {
  background-color: #a74444; /* Darker Accent Red */
  border-color: #a74444;
}

.retry-btn:disabled {
  background-color: var(--color-accent-red); /* Keep color but change opacity */
  opacity: 0.5;
  cursor: not-allowed;
}

.connecting-info {
  display: flex;
  align-items: center;
  gap: 0.5rem; /* 8px */
  font-size: 0.85rem;
  color: var(--color-accent-manila); /* Using Manila */
  background-color: rgba(245, 232, 199, 0.1); /* Manila with low alpha */
  padding: 0.4rem 0.6rem;
  border-radius: 3px;
  margin-top: 0.5rem; /* 8px */
}

.connecting-spinner {
  animation: spin 1.5s linear infinite; /* Slightly faster spin */
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.last-update {
  font-size: 0.8em;
  color: var(--color-text-muted);
  margin-top: 0.5rem; /* 8px */
  padding-top: 0.5rem; /* 8px */
  border-top: 1px solid var(--color-border); /* Use theme border color */
}
</style>
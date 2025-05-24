<template>
  <div class="frontend-client-id">
    <div class="connection-indicator">
      <span :class="connectionClass">●</span>
      {{ connectionText }}
    </div>
    <div class="client-id-text">
      Frontend ID: <span>{{ clientId }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useWebSocketStore } from '../stores/websocket.js'

const wsStore = useWebSocketStore()

const clientId = computed(() => wsStore.frontendClientId)
const connectionStatus = computed(() => wsStore.connectionStatus)

const connectionClass = computed(() => {
  switch (connectionStatus.value) {
    case 'connected':
      return 'connection-connected'
    case 'connecting':
      return 'connection-connecting'
    case 'disconnected':
      return 'connection-disconnected'
    case 'error':
      return 'connection-error'
    default:
      return 'connection-unknown'
  }
})

const connectionText = computed(() => {
  switch (connectionStatus.value) {
    case 'connected':
      return 'Connected'
    case 'connecting':
      return 'Connecting...'
    case 'disconnected':
      return 'Disconnected'
    case 'error':
      return 'Connection Error'
    default:
      return 'Unknown'
  }
})
</script>

<style scoped>
.frontend-client-id {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 0.25rem;
}

.connection-indicator {
  font-size: 0.8em;
  display: flex;
  align-items: center;
  gap: 0.25rem;
}

.client-id-text {
  font-size: 0.85em;
  color: #ADB5BD; /* Lighter variant of Medium Gray */
}

.client-id-text span {
  font-weight: 600;
  color: #F8F9FA;
  font-family: 'Courier New', monospace;
}

.connection-connected {
  color: #10B981; /* Desaturated Green */
}

.connection-connecting {
  color: #F59E0B; /* Desaturated Amber */
  animation: pulse 1.5s ease-in-out infinite alternate;
}

.connection-disconnected {
  color: #6B7280; /* Medium Gray */
}

.connection-error {
  color: #EF4444; /* Desaturated Red */
}

.connection-unknown {
  color: #6B7280; /* Medium Gray */
}

@keyframes pulse {
  from {
    opacity: 1;
  }
  to {
    opacity: 0.5;
  }
}
</style>

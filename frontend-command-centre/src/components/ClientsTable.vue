<template>
  <div class="status-block clients-table">
    <h2>Connected Clients ({{ connectedCount }})</h2>
    <div v-if="!isConnected" class="connection-warning">
      <p>⚠️ WebSocket connection: {{ connectionStatus }}</p>
      <button v-if="connectionStatus === 'error'" @click="retryConnection" class="retry-btn">
        🔄 Retry Connection
      </button>
    </div>
    <table>
      <thead>
        <tr>
          <th>Avatar</th>
          <th>Client ID</th>
          <th>Status</th>
          <th>Last Seen</th>
          <th>Details</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="clients.length === 0">
          <td colspan="6" class="no-clients-message">
            {{ getNoClientsMessage() }}
          </td>
        </tr>
        <ClientTableRow 
          v-for="client in clients" 
          :key="client.id" 
          :client-data="client"
          @action="handleClientAction" 
        />
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import ClientTableRow from './ClientTableRow.vue'
import { useWebSocketStore } from '../stores/websocket.js'

const wsStore = useWebSocketStore()

const clients = computed(() => wsStore.clients)
const connectedCount = computed(() => wsStore.connectedClientsCount)
const isConnected = computed(() => wsStore.isConnected)
const connectionStatus = computed(() => wsStore.connectionStatus)

function getNoClientsMessage() {
  switch (connectionStatus.value) {
    case 'connecting':
      return 'Connecting to server...'
    case 'error':
      return 'Cannot connect to server. Please check if the backend is running and accessible.'
    case 'connected':
      return 'No clients connected or reporting.'
    default:
      return 'Connecting to server...'
  }
}

async function retryConnection() {
  try {
    await wsStore.initialize()
  } catch (error) {
    console.error('Failed to retry connection:', error)
  }
}

async function handleClientAction({ action, clientId }) {
  try {
    let result
    switch (action) {
      case 'pause':
        result = await wsStore.pauseClient(clientId)
        console.log(`Paused client ${clientId}:`, result)
        break
      case 'resume':
        result = await wsStore.resumeClient(clientId)
        console.log(`Resumed client ${clientId}:`, result)
        break
      case 'disconnect':
        result = await wsStore.disconnectClient(clientId)
        console.log(`Disconnected client ${clientId}:`, result)
        break
      default:
        console.warn(`Unknown action: ${action}`)
    }
  } catch (error) {
    console.error(`Failed to ${action} client ${clientId}:`, error)
    // You could add a toast notification here in the future
  }
}
</script>

<style scoped>
.status-block {
  padding: 1rem 1.5rem; /* 16px 24px */
  border: 1px solid var(--color-border); /* Olive Drab/Spy Green */
  border-radius: 4px;
  background-color: var(--color-background-soft); /* Slightly Lighter Background */
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
  /* To make it look like a corner stamp, an alternative would be:
  transform: rotate(45deg);
  top: -15px;
  right: -15px;
  width: 30px;
  height: 30px;
  background-color: rgba(140, 58, 58, 0.6);
  */
}


.clients-table h2 {
  margin-top: 0;
  margin-bottom: 1rem; /* 16px */
  font-size: 1.4rem; /* Slightly increased for 'Special Elite' if it were used here, but it's Roboto Condensed */
  font-weight: 700; /* Bolder for Roboto Condensed heading */
  color: var(--color-heading);
  border-bottom: 2px solid var(--color-text-muted); /* "Redacted" line style */
  padding-bottom: 0.5rem; /* 8px - Fibonacci */
}

.connection-warning {
  background-color: var(--color-accent-red); /* Muted Red */
  border: 1px solid var(--color-text-on-manila); /* Darker border for contrast */
  border-radius: 4px;
  padding: 0.75rem; /* 12px */
  margin-bottom: 1rem; /* 16px */
}

.connection-warning p {
  margin: 0;
  color: var(--color-text-on-accent); /* White text on red background */
  font-weight: 500;
}

.retry-btn {
  background-color: var(--color-background-mute); /* Darker accent */
  color: var(--color-text);
  border: 1px solid var(--color-border);
  padding: 0.5rem 1rem; /* 8px 16px */
  border-radius: 4px;
  cursor: pointer;
  margin-top: 0.5rem; /* 8px */
  transition: background-color 0.3s ease, border-color 0.3s ease;
}

.retry-btn:hover {
  background-color: var(--color-border);
  border-color: var(--color-border-hover);
}

table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 1rem; /* 16px */
}

th, td {
  border: 1px solid var(--color-border); /* Olive Drab */
  padding: 0.625rem 0.75rem; /* 10px 12px, adjusted for Fibonacci feel */
  text-align: left;
  font-size: 0.9rem; /* Maintained from original */
}

th {
  background-color: var(--color-background-mute); /* Darker accent */
  font-weight: 700; /* Bolder for table headers */
  color: var(--color-heading);
  font-family: 'Roboto Condensed', sans-serif; /* Ensure consistency */
  text-shadow: 1px 1px 2px rgba(0, 0, 0, 0.4); /* Subtle shadow for depth */
}

td {
  background-color: var(--color-background); /* Base background for cells */
  color: var(--color-text);
}

.no-clients-message {
  text-align: center;
  color: var(--color-text-muted); /* Muted gray */
  padding: 1.5rem; /* 24px */
  font-style: italic;
}

/* Hover effect for table rows */
tbody tr:hover td {
  background-color: var(--color-background-soft); /* Slightly Lighter Background */
}
</style>

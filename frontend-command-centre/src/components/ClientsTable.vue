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
  padding: 1rem 1.5rem;
  border: 1px solid #495057; /* Subtle Gray */
  border-radius: 4px;
  background-color: #343A40; /* Dark Cool Gray */
}

.clients-table h2 {
  margin-top: 0;
  margin-bottom: 1rem;
  font-size: 1.3rem;
  font-weight: 600;
  color: #F8F9FA; /* Light Gray/Off-White */
  border-bottom: 1px solid #495057;
  padding-bottom: 0.5rem;
}

.connection-warning {
  background-color: #856404; /* Darker amber background */
  border: 1px solid #D97706; /* Desaturated Amber */
  border-radius: 4px;
  padding: 0.75rem;
  margin-bottom: 1rem;
}

.connection-warning p {
  margin: 0;
  color: #FEF3C7; /* Light amber text */
  font-weight: 500;
}

.retry-btn {
  background-color: #6C757D; /* Medium Gray */
  color: #F8F9FA; /* Light Gray/Off-White */
  border: none;
  padding: 0.5rem 1rem;
  border-radius: 4px;
  cursor: pointer;
  margin-top: 0.5rem;
}

.retry-btn:hover {
  background-color: #5A6268; /* Slightly lighter Medium Gray */
}

table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 1rem;
}

th, td {
  border: 1px solid #495057; /* Subtle Gray */
  padding: 0.75rem;
  text-align: left;
  font-size: 0.9em;
}

th {
  background-color: #495057; /* Subtle Gray */
  font-weight: 600;
  color: #F8F9FA; /* Light Gray/Off-White */
}

td {
  background-color: #212529; /* Very Dark Gray */
  color: #F8F9FA; /* Light Gray/Off-White */
}

.no-clients-message {
  text-align: center;
  color: #6C757D; /* Medium Gray */
  padding: 2rem;
  font-style: italic;
}

/* Hover effect for table rows */
tbody tr:hover td {
  background-color: #2C3034; /* Slightly lighter than Very Dark Gray */
}
</style>

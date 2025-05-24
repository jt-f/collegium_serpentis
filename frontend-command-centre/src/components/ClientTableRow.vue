<template>
  <tr>
    <td class="avatar-cell">
      <div class="avatar-placeholder" :style="{ backgroundColor: avatarColor }"></div>
    </td>
    <td class="client-id">{{ clientData.id }}</td>
    <td><span :class="statusClass">{{ clientData.status }}</span></td>
    <td class="last-seen">{{ clientData.lastSeen }}</td>
    <td class="details">{{ clientData.details }}</td>
    <td class="actions-cell">
      <button 
        @click="handleAction('pause')" 
        :disabled="!clientData.canPause"
        class="action-btn pause-btn"
      >
        Pause
      </button>
      <button 
        @click="handleAction('resume')" 
        :disabled="!clientData.canResume"
        class="action-btn resume-btn"
      >
        Resume
      </button>
      <button 
        @click="handleAction('disconnect')" 
        :disabled="!clientData.canDisconnect"
        class="action-btn disconnect-btn"
      >
        Disconnect
      </button>
    </td>
  </tr>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  clientData: {
    type: Object,
    required: true
  }
})

const emit = defineEmits(['action'])

// Generate consistent avatar color based on client ID
const avatarColor = computed(() => {
  if (!props.clientData.id) return '#6C757D'
  
  let hash = 0
  for (let i = 0; i < props.clientData.id.length; i++) {
    hash = props.clientData.id.charCodeAt(i) + ((hash << 5) - hash)
  }
  
  const hue = Math.abs(hash) % 360
  return `hsl(${hue}, 60%, 50%)`
})

const statusClass = computed(() => {
  const status = (props.clientData.status || 'unknown').toLowerCase()
  return `status-${status}`
})

function handleAction(action) {
  emit('action', {
    action,
    clientId: props.clientData.id
  })
}
</script>

<style scoped>
td {
  vertical-align: middle;
}

.client-id {
  font-family: 'Courier New', monospace;
  font-size: 0.85em;
  color: #ADB5BD; /* Lighter Medium Gray */
}

.avatar-cell {
  text-align: center;
}

.avatar-placeholder {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  display: inline-block;
  border: 2px solid #495057; /* Subtle Gray border */
}

.last-seen {
  font-size: 0.85em;
  color: #6C757D; /* Medium Gray */
}

.details {
  font-size: 0.85em;
  color: #ADB5BD; /* Lighter Medium Gray */
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.actions-cell {
  text-align: center;
}

.action-btn {
  margin-right: 0.25rem;
  margin-bottom: 0.25rem;
  padding: 0.3rem 0.6rem;
  font-size: 0.8em;
  border: 1px solid transparent;
  border-radius: 3px;
  cursor: pointer;
  font-weight: 500;
  transition: all 0.2s ease;
}

.action-btn:last-child {
  margin-right: 0;
}

.action-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.pause-btn {
  background-color: #D97706; /* Desaturated Amber */
  border-color: #D97706;
  color: #FEF3C7; /* Light amber text */
}

.pause-btn:hover:not(:disabled) {
  background-color: #B45309;
  border-color: #B45309;
}

.resume-btn {
  background-color: #059669; /* Desaturated Green */
  border-color: #059669;
  color: #D1FAE5; /* Light green text */
}

.resume-btn:hover:not(:disabled) {
  background-color: #047857;
  border-color: #047857;
}

.disconnect-btn {
  background-color: #DC2626; /* Desaturated Red */
  border-color: #DC2626;
  color: #FEE2E2; /* Light red text */
}

.disconnect-btn:hover:not(:disabled) {
  background-color: #B91C1C;
  border-color: #B91C1C;
}

/* Status indicators */
.status-connected {
  color: #10B981; /* Desaturated Green */
  font-weight: 600;
}

.status-disconnected {
  color: #EF4444; /* Desaturated Red */
  font-weight: 600;
}

.status-paused {
  color: #F59E0B; /* Desaturated Amber */
  font-weight: 600;
}

.status-unknown {
  color: #6B7280; /* Medium Gray */
  font-weight: 500;
}
</style>

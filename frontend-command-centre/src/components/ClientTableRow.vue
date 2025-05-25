<template>
  <tr :class="{ vibrating: isUpdating }">
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
import { computed, ref, watch } from 'vue'

const props = defineProps({
  clientData: {
    type: Object,
    required: true
  }
})

const isUpdating = ref(false)

watch(() => props.clientData, (newData, oldData) => {
  if (newData && oldData) {
    // Create copies for comparison, excluding last_seen
    const oldDataComparable = { ...oldData };
    const newDataComparable = { ...newData };
    delete oldDataComparable.last_seen;
    delete newDataComparable.last_seen;

    // Compare stringified versions
    if (JSON.stringify(oldDataComparable) !== JSON.stringify(newDataComparable)) {
      isUpdating.value = true;
      setTimeout(() => {
        isUpdating.value = false;
      }, 500); // Animation duration in ms
    }
  }
}, { deep: true });

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
/* Vibrate Animation */
@keyframes vibrate {
  0% { transform: translate(0); }
  20% { transform: translate(-1px, 1px); }
  40% { transform: translate(-1px, -1px); }
  60% { transform: translate(1px, 1px); }
  80% { transform: translate(1px, -1px); }
  100% { transform: translate(0); }
}

.vibrating {
  animation: vibrate 0.4s linear; /* Duration adjusted to 0.4s for subtlety */
}

td {
  vertical-align: middle;
  /* Using theme variables for text colors now */
}

.client-id {
  font-family: 'Courier New', monospace; /* Keep for "technical" feel */
  font-size: 0.85em;
  color: var(--color-text-muted); /* Using theme variable */
}

.avatar-cell {
  text-align: center;
}

.avatar-placeholder {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  display: inline-block;
  border: 2px solid var(--color-border); /* Using theme variable */
}

.last-seen {
  font-size: 0.85em;
  color: var(--color-text-muted);
}

.details {
  font-size: 0.85em;
  color: var(--color-text);
  max-width: 200px; /* This could be made more responsive if needed */
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}


.actions-cell {
  text-align: center;
}

.action-btn {
  margin-right: 0.25rem; /* 4px */
  margin-bottom: 0.25rem; /* 4px */
  padding: 0.3rem 0.6rem; /* ~5px 10px */
  font-size: 0.8em;
  border: 1px solid var(--color-border); /* Default border */
  border-radius: 3px;
  cursor: pointer;
  font-weight: 500;
  transition: background-color 0.2s ease, color 0.2s ease, border-color 0.2s ease, opacity 0.2s ease;
  color: var(--color-text); /* Default text color */
  background-color: var(--color-background-mute); /* Default background */
}

.action-btn:last-child {
  margin-right: 0;
}

.action-btn:disabled {
  opacity: 0.5; /* Theme consistent disabled opacity */
  cursor: not-allowed;
  background-color: var(--color-background-mute);
  border-color: var(--color-border);
  color: var(--color-text-muted);
}

/* Specific button styles using theme colors */
.pause-btn {
  background-color: var(--color-accent-manila); /* Amber/Yellow */
  border-color: var(--color-accent-manila);
  color: var(--color-text-on-manila); /* Dark text on manila */
}

.pause-btn:hover:not(:disabled) {
  background-color: #e0d4b3; /* Lighter Manila */
  border-color: #e0d4b3;
}

.resume-btn {
  background-color: var(--color-accent-green);
  border-color: var(--color-accent-green);
  color: var(--color-text-on-accent); /* White text on green */
}

.resume-btn:hover:not(:disabled) {
  background-color: var(--color-border-hover); /* Lighter Green */
  border-color: var(--color-border-hover);
}

.disconnect-btn {
  background-color: var(--color-accent-red);
  border-color: var(--color-accent-red);
  color: var(--color-text-on-accent); /* White text on red */
}

.disconnect-btn:hover:not(:disabled) {
  background-color: #a74444; /* Darker Red */
  border-color: #a74444;
}

/* Status indicators using theme variables */
.status-connected {
  color: var(--color-accent-green);
  font-weight: 700;
}

.status-disconnected {
  color: var(--color-accent-red);
  font-weight: 700;
}

.status-paused {
  color: var(--color-accent-manila); /* Amber/Manila */
  font-weight: 700;
}

.status-unknown {
  color: var(--color-text-muted);
  font-weight: 500;
}
</style>

<template>
  <tr :class="{ vibrating: isUpdating }">
    <td class="avatar-cell">
      <div class="avatar-placeholder" :style="{ backgroundColor: avatarColor }"></div>
    </td>
    <td class="client-id">{{ clientData.id }}</td>
    <td class="status-icon-cell">
      <span :title="statusLabel" :aria-label="statusLabel">
        <svg v-if="statusIcon === 'check'" width="18" height="18" viewBox="0 0 18 18" fill="none" xmlns="http://www.w3.org/2000/svg"><circle cx="9" cy="9" r="8" stroke="#556B2F" stroke-width="2"/><path d="M5 10l3 3 5-6" stroke="#556B2F" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        <svg v-else-if="statusIcon === 'x'" width="18" height="18" viewBox="0 0 18 18" fill="none" xmlns="http://www.w3.org/2000/svg"><circle cx="9" cy="9" r="8" stroke="#8C3A3A" stroke-width="2"/><path d="M6 6l6 6M12 6l-6 6" stroke="#8C3A3A" stroke-width="2" stroke-linecap="round"/></svg>
        <svg v-else-if="statusIcon === 'pause'" width="18" height="18" viewBox="0 0 18 18" fill="none" xmlns="http://www.w3.org/2000/svg"><circle cx="9" cy="9" r="8" stroke="#F5E8C7" stroke-width="2"/><rect x="6" y="5" width="2" height="8" rx="1" fill="#F5E8C7"/><rect x="10" y="5" width="2" height="8" rx="1" fill="#F5E8C7"/></svg>
        <svg v-else width="18" height="18" viewBox="0 0 18 18" fill="none" xmlns="http://www.w3.org/2000/svg"><circle cx="9" cy="9" r="8" stroke="#A9A9A9" stroke-width="2"/><text x="9" y="13" text-anchor="middle" font-size="10" fill="#A9A9A9">?</text></svg>
      </span>
    </td>
    <td class="type-cell">{{ clientData.type || '-' }}</td>
    <td class="cpu-cell">
      <div class="gauge-bar" :title="cpuTooltip">
        <div class="gauge-fill cpu" :style="{ width: cpuPercent + '%' }"></div>
      </div>
      <span class="gauge-label">{{ cpuPercent }}%</span>
    </td>
    <td class="ram-cell">
      <div class="gauge-bar" :title="ramTooltip">
        <div class="gauge-fill ram" :style="{ width: ramPercent + '%' }"></div>
      </div>
      <span class="gauge-label">{{ ramPercent }}%</span>
    </td>
    <td class="last-seen">{{ lastSeenClean }}</td>
    <td class="details">
      <span v-if="detailsTooltip" class="details-tooltip" :title="detailsTooltip">&#9432;</span>
    </td>
    <td class="actions-cell">
      <button 
        @click="handleAction('pause')" 
        :disabled="!clientData.canPause"
        class="action-btn pause-btn"
        aria-label="Pause Client"
        title="Pause"
      >
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" xmlns="http://www.w3.org/2000/svg"><rect x="4" y="3" width="3" height="12" rx="1" fill="currentColor"/><rect x="11" y="3" width="3" height="12" rx="1" fill="currentColor"/></svg>
      </button>
      <button 
        @click="handleAction('resume')" 
        :disabled="!clientData.canResume"
        class="action-btn resume-btn"
        aria-label="Resume Client"
        title="Resume"
      >
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" xmlns="http://www.w3.org/2000/svg"><polygon points="5,3 15,9 5,15" fill="currentColor"/></svg>
      </button>
      <button 
        @click="handleAction('disconnect')" 
        :disabled="!clientData.canDisconnect"
        class="action-btn disconnect-btn"
        aria-label="Disconnect Client"
        title="Disconnect"
      >
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none" xmlns="http://www.w3.org/2000/svg"><circle cx="9" cy="9" r="8" stroke="currentColor" stroke-width="2"/><rect x="7" y="5" width="4" height="8" rx="1" fill="currentColor"/></svg>
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

const statusIcon = computed(() => {
  const status = (props.clientData.status || 'unknown').toLowerCase()
  if (status === 'connected') return 'check'
  if (status === 'disconnected') return 'x'
  if (status === 'paused') return 'pause'
  return 'unknown'
})

const statusLabel = computed(() => {
  const status = (props.clientData.status || 'unknown').toLowerCase()
  if (status === 'connected') return 'Connected'
  if (status === 'disconnected') return 'Disconnected'
  if (status === 'paused') return 'Paused'
  return 'Unknown'
})

const cpuPercent = computed(() => {
  const cpu = parseFloat(props.clientData.cpu)
  return isNaN(cpu) ? 0 : Math.round(cpu)
})
const ramPercent = computed(() => {
  const ram = parseFloat(props.clientData.ram)
  return isNaN(ram) ? 0 : Math.round(ram)
})
const cpuTooltip = computed(() => `CPU Usage: ${cpuPercent.value}%`)
const ramTooltip = computed(() => `RAM Usage: ${ramPercent.value}%`)
const lastSeenClean = computed(() => {
  // Remove 'Disconnected:' or 'Last seen:' prefix if present
  return (props.clientData.lastSeen || '').replace(/^(Disconnected:|Last seen:)\s*/i, '')
})
const detailsTooltip = computed(() => {
  // Show all extra metrics or status updates compactly
  if (props.clientData.details && typeof props.clientData.details === 'object') {
    return Object.entries(props.clientData.details)
      .map(([k, v]) => `${k}: ${v}`)
      .join(' | ')
  }
  if (typeof props.clientData.details === 'string') {
    return props.clientData.details
  }
  return ''
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
  /* Removed border-right, border-bottom, padding - these are now controlled by ClientsTable.vue */
  /* font-family and font-size are also controlled by ClientsTable.vue for consistency */
}

.client-id {
  font-family: 'Courier New', monospace; /* Keep for "technical" feel if desired, otherwise ClientsTable.vue style will apply */
  font-size: 0.85em; /* Can keep if a smaller font for ID is desired */
  color: var(--color-text-muted);
  /* Remove any border or padding here */
}

.avatar-cell {
  text-align: center;
  /* Remove any border or padding here */
}

.avatar-placeholder {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  display: inline-block;
  border: 2px solid var(--color-border); /* This border is for the avatar itself, not the cell */
}

.type-cell {
  text-align: center;
  /* color: var(--color-text); - Handled by ClientsTable.vue */
  /* font-size: 0.95em; - Handled by ClientsTable.vue */
  /* Remove any border or padding here */
}

.cpu-cell, .ram-cell {
  min-width: 80px; /* This is fine for column sizing */
  text-align: center;
  /* Remove any border or padding here */
}

.gauge-bar {
  width: 60px;
  height: 10px;
  background: var(--color-background-mute);
  border-radius: 5px;
  overflow: hidden;
  display: inline-block;
  vertical-align: middle;
  margin-right: 0.3em;
}

.gauge-fill {
  height: 100%;
  border-radius: 5px;
  transition: width 0.3s;
}

.gauge-fill.cpu {
  background: linear-gradient(90deg, #6B8E23, #F5E8C7);
}

.gauge-fill.ram {
  background: linear-gradient(90deg, #6A82A4, #F5E8C7);
}

.gauge-label {
  font-size: 0.85em;
  color: var(--color-text-muted);
}

.details-tooltip {
  cursor: pointer;
  color: var(--color-accent-blue);
  font-size: 1.2em;
  margin-left: 0.2em;
}

.last-seen {
  /* font-size: 0.85em; - Handled by ClientsTable.vue or keep if smaller font is desired */
  color: var(--color-text-muted);
  /* Remove any border or padding here */
}

.status-icon-cell {
  text-align: center;
  /* Remove any border or padding here */
}

.actions-cell {
  text-align: center;
  display: flex;
  flex-direction: row;
  justify-content: center;
  align-items: center;
  gap: 0.25rem;
  /* Remove any border or padding here */
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

.action-btn svg {
  vertical-align: middle;
  display: inline-block;
}
.action-btn:hover:not(:disabled) {
  filter: brightness(1.2);
  box-shadow: 0 0 4px var(--color-border-hover);
}
</style>

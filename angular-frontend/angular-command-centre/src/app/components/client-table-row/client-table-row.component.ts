import { Component, Input, Output, EventEmitter, OnInit, OnChanges, SimpleChanges, ChangeDetectionStrategy, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Client } from '../../services/websocket.service'; // Assuming Client interface is exported

@Component({
  selector: '[app-client-table-row]', // Corrected to be an attribute selector
  standalone: true,
  imports: [CommonModule],
  templateUrl: './client-table-row.component.html',
  styleUrl: './client-table-row.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush // Use OnPush for performance
})
export class ClientTableRowComponent implements OnInit, OnChanges {
  @Input() clientData!: Client; // Use definite assignment assertion
  @Output() action = new EventEmitter<{ action: string, clientId: string }>();

  isUpdating: boolean = false;

  constructor(private cdr: ChangeDetectorRef) {}

  ngOnInit(): void {
    // Initial processing if needed, though getters handle most dynamic data
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['clientData']) {
      const oldData = changes['clientData'].previousValue;
      const newData = changes['clientData'].currentValue;

      if (oldData && newData && JSON.stringify(this.getComparable(oldData)) !== JSON.stringify(this.getComparable(newData))) {
        this.isUpdating = true;
        setTimeout(() => {
          this.isUpdating = false;
          this.cdr.detectChanges(); // Trigger change detection after animation
        }, 400); // Animation duration
      }
    }
  }

  private getComparable(data: Client): Partial<Client> {
    const { lastSeen, ...comparableData } = data; // Exclude lastSeen for comparison
    return comparableData;
  }

  get avatarColor(): string {
    if (!this.clientData?.id) return '#6C757D';
    let hash = 0;
    for (let i = 0; i < this.clientData.id.length; i++) {
      hash = this.clientData.id.charCodeAt(i) + ((hash << 5) - hash);
    }
    const hue = Math.abs(hash) % 360;
    return `hsl(${hue}, 60%, 50%)`;
  }

  get statusIcon(): string {
    const status = (this.clientData?.status || 'unknown').toLowerCase();
    if (status === 'connected') return 'check';
    if (status === 'disconnected') return 'x';
    if (status === 'paused') return 'pause';
    return 'unknown';
  }

  get statusLabel(): string {
    const status = (this.clientData?.status || 'unknown').toLowerCase();
    if (status === 'connected') return 'Connected';
    if (status === 'disconnected') return 'Disconnected';
    if (status === 'paused') return 'Paused';
    return 'Unknown';
  }

  get cpuPercent(): number {
    const cpu = parseFloat(this.clientData?.cpu_usage); // Vue used clientData.cpu
    return isNaN(cpu) ? 0 : Math.round(cpu);
  }

  get ramPercent(): number {
    const ram = parseFloat(this.clientData?.memory_usage); // Vue used clientData.ram
    return isNaN(ram) ? 0 : Math.round(ram);
  }

  get cpuTooltip(): string {
    return `CPU Usage: ${this.cpuPercent}%`;
  }

  get ramTooltip(): string {
    return `RAM Usage: ${this.ramPercent}%`;
  }

  get lastSeenClean(): string {
    return (this.clientData?.lastSeen || '').replace(/^(Disconnected:|Last seen:)\s*/i, '');
  }

  get detailsTooltip(): string {
    if (this.clientData?.details && typeof this.clientData.details === 'object') {
      return Object.entries(this.clientData.details)
        .map(([k, v]) => `${k}: ${v}`)
        .join(' | ');
    }
    if (typeof this.clientData?.details === 'string') {
      return this.clientData.details;
    }
    return '';
  }

  handleAction(actionName: string): void {
    this.action.emit({ action: actionName, clientId: this.clientData.id });
  }
}

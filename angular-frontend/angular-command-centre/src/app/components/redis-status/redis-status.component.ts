import { Component, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { WebsocketService } from '../../services/websocket.service';
import { Subscription } from 'rxjs';
// Removed map import as it's not directly used in this version of the TS file.
// If future refactoring uses .pipe(map(...)) for observables, it should be re-added.

@Component({
  selector: 'app-redis-status',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './redis-status.component.html',
  styleUrl: './redis-status.component.scss'
})
export class RedisStatusComponent implements OnInit, OnDestroy {
  redisStatus: string = 'unknown';
  lastUpdate: string | null = null;

  // Subscriptions
  private statusSubscription!: Subscription;
  private lastUpdateSubscription!: Subscription;

  // Properties for template binding (derived from subscriptions)
  // These will be updated whenever redisStatus or lastUpdate changes.
  // No need for separate observables for each class/text if updated directly.

  constructor(public websocketService: WebsocketService) {}

  ngOnInit(): void {
    this.statusSubscription = this.websocketService.redisStatus.subscribe(status => {
      this.redisStatus = status;
      // Potentially update dependent properties here if not using getters in template
    });
    this.lastUpdateSubscription = this.websocketService.lastUpdate.subscribe(update => {
      this.lastUpdate = update;
    });
  }

  ngOnDestroy(): void {
    if (this.statusSubscription) this.statusSubscription.unsubscribe();
    if (this.lastUpdateSubscription) this.lastUpdateSubscription.unsubscribe();
  }

  get statusText(): string {
    switch (this.redisStatus) {
      case 'connected': return 'Operational';
      case 'unavailable': return 'System Offline';
      case 'error': return 'Compromised';
      default: return 'Status Unknown';
    }
  }

  get statusTextClass(): string {
    switch (this.redisStatus) {
      case 'connected': return 'status-connected';
      case 'unavailable': case 'error': return 'status-error';
      default: return 'status-unknown';
    }
  }

  get indicatorClass(): string {
    switch (this.redisStatus) {
      case 'connected': return 'indicator-connected';
      case 'unavailable': case 'error': return 'indicator-error';
      default: return 'indicator-unknown';
    }
  }

  get triangleClass(): string {
    switch (this.redisStatus) {
      case 'connected': return 'triangle-green';
      case 'unavailable': case 'error': return 'triangle-red';
      default: return 'triangle-gray';
    }
  }

  formatTime(isoString: string | null): string {
    if (!isoString) return '';
    return new Date(isoString).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  }
}

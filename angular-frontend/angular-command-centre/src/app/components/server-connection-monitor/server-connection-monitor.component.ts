import { Component, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { WebsocketService } from '../../services/websocket.service';
import { Subscription, Observable } from 'rxjs';
import { map } from 'rxjs/operators';

@Component({
  selector: 'app-server-connection-monitor',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './server-connection-monitor.component.html',
  styleUrl: './server-connection-monitor.component.scss'
})
export class ServerConnectionMonitorComponent implements OnInit, OnDestroy {
  connectionStatus: string = 'disconnected';
  isConnected: boolean = false;
  lastUpdate: string | null = null;
  reconnectAttempts$: Observable<number>;
  isRetrying: boolean = false;

  private subscriptions: Subscription = new Subscription();

  constructor(public websocketService: WebsocketService) {
    this.reconnectAttempts$ = this.websocketService.reconnectAttempts.asObservable();
  }

  ngOnInit(): void {
    this.subscriptions.add(this.websocketService.connectionStatus.subscribe(status => {
      this.connectionStatus = status;
      if (status === 'error' || status === 'connected') { // Stop retrying visual state if connection resolved
        this.isRetrying = false;
      }
    }));
    this.subscriptions.add(this.websocketService.isConnected$.subscribe(connected => {
      this.isConnected = connected;
    }));
    this.subscriptions.add(this.websocketService.lastUpdate.subscribe(update => {
      this.lastUpdate = update;
    }));
  }

  ngOnDestroy(): void {
    this.subscriptions.unsubscribe();
  }

  get statusText(): string {
    switch (this.connectionStatus) {
      case 'connected': return 'Connected';
      case 'connecting': return 'Connecting';
      case 'disconnected': return 'Disconnected';
      case 'error': return 'Connection Failed';
      default: return 'Unknown';
    }
  }

  get statusClass(): string {
    switch (this.connectionStatus) {
      case 'connected': return 'status-connected';
      case 'connecting': return 'status-connecting';
      case 'disconnected': return 'status-disconnected';
      case 'error': return 'status-error';
      default: return 'status-unknown';
    }
  }

  get statusIndicatorClass(): string {
    switch (this.connectionStatus) {
      case 'connected': return 'indicator-connected';
      case 'connecting': return 'indicator-connecting';
      case 'disconnected': return 'indicator-disconnected';
      case 'error': return 'indicator-error';
      default: return 'indicator-unknown';
    }
  }

  get triangleClass(): string {
    switch (this.connectionStatus) {
      case 'connected': return 'triangle-green';
      case 'connecting': return 'triangle-yellow';
      case 'error': return 'triangle-red';
      case 'disconnected': return 'triangle-gray';
      default: return 'triangle-gray';
    }
  }

  async retryConnection(): Promise<void> {
    this.isRetrying = true;
    try {
      // Reset reconnect attempts before initializing, so it doesn't immediately give up if max attempts were reached.
      this.websocketService.reconnectAttempts.next(0);
      await this.websocketService.initialize();
    } catch (error) {
      console.error('Failed to retry connection via ServerConnectionMonitor:', error);
      // isRetrying will be set to false by the status subscription if connection fails again
    }
    // No finally block to set isRetrying = false, as the status subscription should handle this.
    // However, if initialize() itself fails without changing status, this could be an issue.
    // For now, relying on status change.
  }

  formatTime(isoString: string | null): string {
    if (!isoString) return '';
    // Ensure this matches the Vue version's output if specific format is key
    return new Date(isoString).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  }
}

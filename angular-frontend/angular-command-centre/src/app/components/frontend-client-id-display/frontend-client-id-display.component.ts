import { Component, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common'; // Import CommonModule
import { WebsocketService } from '../../services/websocket.service';
import { Subscription } from 'rxjs';

@Component({
  selector: 'app-frontend-client-id-display',
  standalone: true, // Mark as standalone
  imports: [CommonModule], // Import CommonModule here
  templateUrl: './frontend-client-id-display.component.html',
  styleUrl: './frontend-client-id-display.component.scss'
})
export class FrontendClientIdDisplayComponent implements OnInit, OnDestroy {
  clientId: string = '';
  connectionStatus: string = 'disconnected';
  private statusSubscription!: Subscription;

  constructor(private websocketService: WebsocketService) {
    this.clientId = this.websocketService.frontendClientId;
  }

  ngOnInit(): void {
    this.statusSubscription = this.websocketService.connectionStatus.subscribe(status => {
      this.connectionStatus = status;
    });
  }

  ngOnDestroy(): void {
    if (this.statusSubscription) {
      this.statusSubscription.unsubscribe();
    }
  }

  get connectionClass(): string {
    switch (this.connectionStatus) {
      case 'connected':
        return 'connection-connected';
      case 'connecting':
        return 'connection-connecting';
      case 'disconnected':
        return 'connection-disconnected';
      case 'error':
        return 'connection-error';
      default:
        return 'connection-unknown';
    }
  }

  get connectionText(): string {
    switch (this.connectionStatus) {
      case 'connected':
        return 'Connected';
      case 'connecting':
        return 'Connecting...';
      case 'disconnected':
        return 'Disconnected';
      case 'error':
        return 'Connection Error';
      default:
        return 'Unknown';
    }
  }
}

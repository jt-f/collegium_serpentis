import { Component, OnInit } from '@angular/core'; // Import OnInit
import { RouterOutlet } from '@angular/router';
import { FrontendClientIdDisplayComponent } from './components/frontend-client-id-display/frontend-client-id-display.component';
import { RedisStatusComponent } from './components/redis-status/redis-status.component';
import { ServerConnectionMonitorComponent } from './components/server-connection-monitor/server-connection-monitor.component';
import { PlaceholderBlockComponent } from './components/placeholder-block/placeholder-block.component';
import { ClientsTableComponent } from './components/clients-table/clients-table.component';
import { WebsocketService } from './services/websocket.service'; // Import WebsocketService

@Component({
  selector: 'app-root',
  imports: [
    RouterOutlet,
    FrontendClientIdDisplayComponent,
    RedisStatusComponent,
    ServerConnectionMonitorComponent,
    PlaceholderBlockComponent,
    ClientsTableComponent
  ],
  templateUrl: './app.component.html',
  styleUrl: './app.component.scss'
})
export class AppComponent implements OnInit { // Implement OnInit
  title = 'angular-command-centre';

  constructor(private websocketService: WebsocketService) {} // Inject WebsocketService

  ngOnInit(): void {
    this.websocketService.initialize(); // Initialize WebSocket connection
  }
}

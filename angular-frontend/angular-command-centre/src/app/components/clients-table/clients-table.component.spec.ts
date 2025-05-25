import { ComponentFixture, TestBed } from '@angular/core/testing';
import { CommonModule } from '@angular/common';
import { ClientsTableComponent } from './clients-table.component';
import { WebsocketService } from '../../services/websocket.service';
import { of } from 'rxjs';
import { NO_ERRORS_SCHEMA } from '@angular/core'; // To ignore app-client-table-row for now

// Mock WebsocketService
class MockWebsocketService {
  clientsList$ = of([]);
  connectedClientsCount$ = of(0);
  isConnected$ = of(true);
  connectionStatus$ = of('connected');
  initialize = jasmine.createSpy('initialize');
  pauseClient = jasmine.createSpy('pauseClient');
  resumeClient = jasmine.createSpy('resumeClient');
  disconnectClient = jasmine.createSpy('disconnectClient');
  reconnectAttempts = of(0); // Add missing property
}

describe('ClientsTableComponent', () => {
  let component: ClientsTableComponent;
  let fixture: ComponentFixture<ClientsTableComponent>;
  let mockWebsocketService: MockWebsocketService; // Use the class directly for typing

  beforeEach(async () => {
    // Re-initialize mock for each test to ensure clean spies and subjects
    mockWebsocketService = new MockWebsocketService(); 

    await TestBed.configureTestingModule({
      imports: [CommonModule, ClientsTableComponent],
      providers: [
        { provide: WebsocketService, useValue: mockWebsocketService } // Use useValue with instance
      ],
      schemas: [NO_ERRORS_SCHEMA]
    })
    .compileComponents();

    fixture = TestBed.createComponent(ClientsTableComponent);
    component = fixture.componentInstance;
    // mockWebsocketService is already injected via useValue
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  describe('getNoClientsMessage', () => {
    it('should return "Connecting to server..." when status is "connecting"', () => {
      // To test getNoClientsMessage, we need to control currentConnectionStatus,
      // which is subscribed to connectionStatus$. We can update the source observable.
      (mockWebsocketService.connectionStatus$ as any).next('connecting');
      fixture.detectChanges(); // Re-run change detection to update subscription
      expect(component.getNoClientsMessage()).toBe('Connecting to server...');
    });

    it('should return "Cannot connect to server..." when status is "error"', () => {
      (mockWebsocketService.connectionStatus$ as any).next('error');
      fixture.detectChanges();
      expect(component.getNoClientsMessage()).toBe('Cannot connect to server. Please check if the backend is running and accessible.');
    });

    it('should return "No clients connected..." when status is "connected" and no clients', () => {
      (mockWebsocketService.connectionStatus$ as any).next('connected');
      (mockWebsocketService.clientsList$ as any).next([]); // Ensure clients list is empty
      fixture.detectChanges();
      expect(component.getNoClientsMessage()).toBe('No clients connected or reporting.');
    });
  });

  describe('handleClientAction', () => {
    it('should call pauseClient on WebsocketService for "pause" action', async () => {
      await component.handleClientAction({ action: 'pause', clientId: 'test-id-123' });
      expect(mockWebsocketService.pauseClient).toHaveBeenCalledWith('test-id-123');
    });

    it('should call resumeClient on WebsocketService for "resume" action', async () => {
      await component.handleClientAction({ action: 'resume', clientId: 'test-id-123' });
      expect(mockWebsocketService.resumeClient).toHaveBeenCalledWith('test-id-123');
    });

    it('should call disconnectClient on WebsocketService for "disconnect" action', async () => {
      await component.handleClientAction({ action: 'disconnect', clientId: 'test-id-123' });
      expect(mockWebsocketService.disconnectClient).toHaveBeenCalledWith('test-id-123');
    });
  });
  
  describe('retryConnection', () => {
    it('should call initialize on WebsocketService and reset reconnectAttempts', async () => {
      // Spy on the BehaviorSubject's next method for reconnectAttempts
      const reconnectAttemptsSpy = spyOn(mockWebsocketService.reconnectAttempts as any, 'next');
      await component.retryConnection();
      expect(reconnectAttemptsSpy).toHaveBeenCalledWith(0);
      expect(mockWebsocketService.initialize).toHaveBeenCalled();
    });
  });

  it('should render table headers', () => {
    const compiled = fixture.nativeElement as HTMLElement;
    const headers = compiled.querySelectorAll('th');
    const headerTexts = Array.from(headers).map(th => th.textContent);
    expect(headerTexts).toContain('Client ID');
    expect(headerTexts).toContain('Status');
    expect(headerTexts).toContain('Type');
    expect(headerTexts).toContain('CPU');
    expect(headerTexts).toContain('RAM');
    expect(headerTexts).toContain('Last Seen');
    expect(headerTexts).toContain('Actions');
  });

  it('should display "No clients connected or reporting." message when connected and no clients', () => {
    (mockWebsocketService.isConnected$ as any).next(true);
    (mockWebsocketService.clientsList$ as any).next([]);
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    const noClientsMessageElement = compiled.querySelector('.no-clients-message');
    expect(noClientsMessageElement).toBeTruthy();
    expect(noClientsMessageElement?.textContent?.trim()).toBe('No clients connected or reporting.');
  });
});

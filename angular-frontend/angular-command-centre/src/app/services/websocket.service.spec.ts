import { TestBed, fakeAsync, tick } from '@angular/core/testing';
import { WebsocketService } from './websocket.service';
import * as angularCore from '@angular/core'; // Import as namespace
import { firstValueFrom, of } from 'rxjs';

describe('WebsocketService', () => {
  let service: WebsocketService;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [WebsocketService]
    });
    service = TestBed.inject(WebsocketService);
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('should have a frontendClientId defined', () => {
    expect(service.frontendClientId).toBeDefined();
    expect(service.frontendClientId.startsWith('fe-')).toBeTrue();
  });

  it('should have initial connectionStatus as "disconnected"', async () => {
    const status = await firstValueFrom(service.connectionStatus);
    expect(status).toBe('disconnected');
  });

  it('should have initial redisStatus as "unknown"', async () => {
    const status = await firstValueFrom(service.redisStatus);
    expect(status).toBe('unknown');
  });

  describe('wsUrl getter', () => {
    it('should return correct URL in dev mode', () => {
      spyOnProperty(angularCore, 'isDevMode', 'get').and.returnValue(() => true);
      // Need to re-initialize service or directly call getter if possible,
      // but TestBed creates service before spy. So, create a new instance for this test.
      const devService = new WebsocketService();
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const host = window.location.host;
      expect(devService.wsUrl).toBe(`${protocol}//${host}/ws`);
    });

    it('should return correct URL in prod mode', () => {
      spyOnProperty(angularCore, 'isDevMode', 'get').and.returnValue(() => false);
      const prodService = new WebsocketService();
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const host = window.location.host;
      expect(prodService.wsUrl).toBe(`${protocol}//${host}/ws`);
    });
  });

  describe('initialize', () => {
    let checkServerHealthSpy: jasmine.Spy;
    let connectSpy: jasmine.Spy;

    beforeEach(() => {
      // Spy on methods of the instance provided by TestBed
      checkServerHealthSpy = spyOn(service as any, 'checkServerHealth').and.callThrough();
      connectSpy = spyOn(service, 'connect').and.stub(); // Stub connect to prevent actual WebSocket connection
      spyOn(service as any, 'fetchInitialData').and.resolveTo(); // Prevent actual fetch
      spyOn(service as any, 'startPeriodicRefresh').and.stub(); // Stub periodic refresh
    });

    it('should call checkServerHealth', fakeAsync(() => {
      checkServerHealthSpy.and.resolveTo(true); // Mock server health check
      service.initialize();
      tick(); // Allow async operations to complete
      expect(checkServerHealthSpy).toHaveBeenCalled();
    }));

    it('should attempt to connect if server health is good', fakeAsync(() => {
      checkServerHealthSpy.and.resolveTo(true);
      service.initialize();
      tick();
      expect(connectSpy).toHaveBeenCalled();
    }));

    it('should set connectionStatus to "error" if server health check fails', fakeAsync(() => {
      checkServerHealthSpy.and.resolveTo(false);
      service.initialize();
      tick();
      expect(service.connectionStatus.value).toBe('error');
      expect(connectSpy).not.toHaveBeenCalled();
    }));
  });

  describe('checkServerHealth (mocking fetch)', () => {
    it('should return true when fetch is ok', async () => {
      spyOn(window, 'fetch').and.resolveTo({ ok: true } as Response);
      const result = await (service as any).checkServerHealth();
      expect(result).toBeTrue();
    });

    it('should return false when fetch is not ok', async () => {
      spyOn(window, 'fetch').and.resolveTo({ ok: false } as Response);
      const result = await (service as any).checkServerHealth();
      expect(result).toBeFalse();
    });

    it('should return false when fetch throws an error', async () => {
      spyOn(window, 'fetch').and.rejectWith(new Error('Network error'));
      const result = await (service as any).checkServerHealth();
      expect(result).toBeFalse();
    });
  });

  // Add more tests for other methods like updateClientData, handleMessage etc.
  // These would require more complex mocking of WebSocket behavior.
});

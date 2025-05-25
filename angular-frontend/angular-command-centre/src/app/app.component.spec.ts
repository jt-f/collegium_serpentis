import { TestBed, ComponentFixture } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { AppComponent } from './app.component';
import { WebsocketService } from './services/websocket.service';
import { NO_ERRORS_SCHEMA } from '@angular/core'; // To ignore unknown elements like child components

// Create a mock WebsocketService
class MockWebsocketService {
  initialize = jasmine.createSpy('initialize');
  // Add other methods and properties if needed by AppComponent during its lifecycle
}

describe('AppComponent', () => {
  let fixture: ComponentFixture<AppComponent>;
  let app: AppComponent;
  let mockWebsocketService: MockWebsocketService;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AppComponent], // AppComponent is standalone, so it's directly in imports
      providers: [
        { provide: WebsocketService, useClass: MockWebsocketService }
      ],
      schemas: [NO_ERRORS_SCHEMA] // Add NO_ERRORS_SCHEMA to suppress errors for child components not declared in test
    }).compileComponents();

    fixture = TestBed.createComponent(AppComponent);
    app = fixture.componentInstance;
    mockWebsocketService = TestBed.inject(WebsocketService) as unknown as MockWebsocketService;
  });

  it('should create the app', () => {
    expect(app).toBeTruthy();
  });

  it(`should have the 'angular-command-centre' title`, () => {
    expect(app.title).toEqual('angular-command-centre');
  });

  it('should call WebsocketService.initialize on ngOnInit', () => {
    fixture.detectChanges(); // Triggers ngOnInit
    expect(mockWebsocketService.initialize).toHaveBeenCalled();
  });

  // Test for the presence of structural elements/child component selectors
  // These tests assume that the child components are correctly imported in app.component.ts
  // and that their selectors are as expected.
  it('should render the FrontendClientIdDisplay component', () => {
    fixture.detectChanges();
    const element = fixture.debugElement.query(By.css('app-frontend-client-id-display'));
    expect(element).not.toBeNull();
  });

  it('should render the RedisStatus component', () => {
    fixture.detectChanges();
    const element = fixture.debugElement.query(By.css('app-redis-status'));
    expect(element).not.toBeNull();
  });

  it('should render the ServerConnectionMonitor component', () => {
    fixture.detectChanges();
    const element = fixture.debugElement.query(By.css('app-server-connection-monitor'));
    expect(element).not.toBeNull();
  });

  it('should render the ClientsTable component', () => {
    fixture.detectChanges();
    const element = fixture.debugElement.query(By.css('app-clients-table'));
    expect(element).not.toBeNull();
  });
  
  // The original "should render title" test is removed as the default h1 is no longer in app.component.html
  // If there's a specific title element to test (e.g., within the header), that can be added.
  // For example, checking the h1 inside the .app-header:
  it('should render the main header title "Command Centre"', () => {
    fixture.detectChanges();
    const compiled = fixture.nativeElement as HTMLElement;
    const headerH1 = compiled.querySelector('.app-header h1');
    expect(headerH1?.textContent).toContain('Command Centre');
  });

});

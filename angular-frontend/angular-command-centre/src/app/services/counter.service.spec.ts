import { TestBed } from '@angular/core/testing';
import { CounterService } from './counter.service';
import { firstValueFrom } from 'rxjs'; // For getting the first value of an observable

describe('CounterService', () => {
  let service: CounterService;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    service = TestBed.inject(CounterService);
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('should have an initial count of 0', (done) => {
    service.count$.subscribe(count => {
      expect(count).toBe(0);
      done();
    });
  });

  it('should increment count correctly', (done) => {
    service.increment();
    service.count$.subscribe(count => {
      expect(count).toBe(1);
      service.increment(); // Increment again
      // Need to resubscribe or use a different approach to get the latest value for the second check
    });
    // A better way for multiple increments:
    service.increment(); // count is now 1
    service.increment(); // count is now 2
    service.count$.subscribe(count => {
      expect(count).toBe(2);
      done();
    });
  });
  
  // A more robust way to test increment:
  it('should increment count correctly (robust)', async () => {
    let currentCount = await firstValueFrom(service.count$);
    expect(currentCount).toBe(0);

    service.increment();
    currentCount = await firstValueFrom(service.count$);
    expect(currentCount).toBe(1);

    service.increment();
    currentCount = await firstValueFrom(service.count$);
    expect(currentCount).toBe(2);
  });

  it('should emit correct doubled value from doubleCount$', async () => {
    // Initial value
    let doubleCount = await firstValueFrom(service.doubleCount$);
    expect(doubleCount).toBe(0);

    // After first increment
    service.increment(); // count is 1
    doubleCount = await firstValueFrom(service.doubleCount$);
    expect(doubleCount).toBe(2);

    // After second increment
    service.increment(); // count is 2
    doubleCount = await firstValueFrom(service.doubleCount$);
    expect(doubleCount).toBe(4);
  });
});

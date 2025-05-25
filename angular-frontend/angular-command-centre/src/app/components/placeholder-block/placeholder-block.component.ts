import { Component, Input } from '@angular/core';

@Component({
  selector: 'app-placeholder-block',
  // imports: [], // Not needed for this component with basic types and no other modules directly.
  // For standalone components, CommonModule (for *ngIf, *ngFor, etc.) is often imported here if needed.
  // Since we are only using Input properties and basic HTML, it might not be strictly necessary.
  // However, Angular CLI usually adds `CommonModule` by default to new components if they are standalone and use such directives.
  // Let's assume it's not needed for now and can be added if template errors occur.
  templateUrl: './placeholder-block.component.html',
  styleUrl: './placeholder-block.component.scss'
})
export class PlaceholderBlockComponent {
  @Input() title: string = '';
  @Input() content: string = '';
}

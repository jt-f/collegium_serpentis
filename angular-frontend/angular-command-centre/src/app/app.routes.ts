import { Routes } from '@angular/router';
import { HomeViewComponent } from './views/home-view/home-view.component';
import { AboutViewComponent } from './views/about-view/about-view.component';

export const routes: Routes = [
  { path: '', component: HomeViewComponent },
  { path: 'about', component: AboutViewComponent },
];

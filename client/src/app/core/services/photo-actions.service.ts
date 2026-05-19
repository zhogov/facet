import { Injectable, inject } from '@angular/core';
import { MatDialog } from '@angular/material/dialog';
import { MatSnackBar } from '@angular/material/snack-bar';
import { Photo } from '../../shared/models/photo.model';
import { GalleryStore } from '../../features/gallery/gallery.store';
import { I18nService } from './i18n.service';

@Injectable({ providedIn: 'root' })
export class PhotoActionsService {
  private readonly dialog = inject(MatDialog);
  private readonly store = inject(GalleryStore);
  private readonly snackBar = inject(MatSnackBar);
  private readonly i18n = inject(I18nService);

  openCritique(photo: Photo): void {
    import('../../features/gallery/photo-critique-dialog.component').then(m => {
      const vlmAvailable = this.store.config()?.features?.show_vlm_critique ?? false;
      this.dialog.open(m.PhotoCritiqueDialogComponent, {
        data: { photoPath: photo.path, vlmAvailable },
        width: '95vw',
        maxWidth: '600px',
      });
    });
  }

  openAddPerson(photo: Photo, onAssigned?: () => void): void {
    import('../../features/gallery/face-selector-dialog.component').then(m => {
      const faceRef = this.dialog.open(m.FaceSelectorDialogComponent, {
        data: { photoPath: photo.path },
        width: '95vw',
        maxWidth: '400px',
      });
      faceRef.afterClosed().subscribe(face => {
        if (!face) return;
        import('../../features/gallery/person-selector-dialog.component').then(m2 => {
          const persons = this.store.persons().filter(p => p.name);
          const personRef = this.dialog.open(m2.PersonSelectorDialogComponent, {
            data: persons,
            width: '95vw',
            maxWidth: '400px',
          });
          personRef.afterClosed().subscribe(async result => {
            if (!result) return;
            if (result.kind === 'create') {
              const created = await this.store.createPerson(result.name, [face.id], photo.path);
              if (created) {
                this.snackBar.open(this.i18n.t('notifications.faces_assigned'), '', { duration: 2000 });
                onAssigned?.();
              }
            } else if (result.kind === 'select') {
              await this.store.assignFace(face.id, result.person.id, photo.path, result.person.name);
              this.snackBar.open(this.i18n.t('notifications.faces_assigned'), '', { duration: 2000 });
              onAssigned?.();
            }
          });
        });
      });
    });
  }
}

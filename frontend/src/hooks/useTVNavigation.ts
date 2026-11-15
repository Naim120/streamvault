import { useEffect } from 'react';
import { useUIStore } from '../store/useUIStore';
import { usePlayerStore } from '../store/usePlayerStore';

/**
 * 2D Spatial Remote / D-Pad Navigation Hook for JioOS / Android TV
 * Enables smooth 10-foot 2D arrow navigation, active modal trapping,
 * Leanback back button routing, and remote media key dispatching.
 */
export function useTVNavigation() {
  useEffect(() => {
    if (typeof window === 'undefined') return;

    const isInputActive = (): boolean => {
      const activeEl = document.activeElement;
      if (!activeEl) return false;
      const tag = activeEl.tagName.toUpperCase();
      return (
        tag === 'INPUT' ||
        tag === 'TEXTAREA' ||
        tag === 'SELECT' ||
        (activeEl as HTMLElement).isContentEditable
      );
    };

    const getCandidateScope = (): HTMLElement | Document => {
      // If any modal/dialog is visible on top of the screen, constrain focus to that modal
      const modals = document.querySelectorAll<HTMLElement>(
        '[role="dialog"], .fixed.inset-0:not(.pointer-events-none), [data-modal="true"]'
      );
      for (let i = modals.length - 1; i >= 0; i--) {
        const modal = modals[i];
        const rect = modal.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0 && window.getComputedStyle(modal).display !== 'none') {
          return modal;
        }
      }
      return document;
    };

    const getVisibleFocusableElements = (scope: HTMLElement | Document): HTMLElement[] => {
      const selector =
        'button:not([disabled]), [tabindex="0"], a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [data-tv-card="true"]';
      const all = Array.from(scope.querySelectorAll<HTMLElement>(selector));

      return all.filter((el) => {
        if (el.getAttribute('aria-hidden') === 'true') return false;
        if (el.tabIndex < 0) return false;
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return false;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
        return true;
      });
    };

    const navigateDirection = (direction: 'ArrowUp' | 'ArrowDown' | 'ArrowLeft' | 'ArrowRight'): boolean => {
      const scope = getCandidateScope();
      const candidates = getVisibleFocusableElements(scope);
      if (candidates.length === 0) return false;

      let current = document.activeElement as HTMLElement | null;
      if (!current || !scope.contains(current) || current === document.body) {
        // No current focused candidate inside scope -> focus first available element
        candidates[0]?.focus();
        candidates[0]?.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
        return true;
      }

      const currRect = current.getBoundingClientRect();
      const currCx = currRect.left + currRect.width / 2;
      const currCy = currRect.top + currRect.height / 2;

      let bestMatch: HTMLElement | null = null;
      let minDistance = Infinity;

      for (const cand of candidates) {
        if (cand === current) continue;
        const candRect = cand.getBoundingClientRect();
        const candCx = candRect.left + candRect.width / 2;
        const candCy = candRect.top + candRect.height / 2;

        let isValid = false;
        let primaryDist = 0;
        let secondaryDist = 0;

        switch (direction) {
          case 'ArrowRight':
            if (candCx > currCx + 4) {
              isValid = true;
              primaryDist = candCx - currCx;
              secondaryDist = Math.abs(candCy - currCy);
            }
            break;
          case 'ArrowLeft':
            if (candCx < currCx - 4) {
              isValid = true;
              primaryDist = currCx - candCx;
              secondaryDist = Math.abs(candCy - currCy);
            }
            break;
          case 'ArrowDown':
            if (candCy > currCy + 4) {
              isValid = true;
              primaryDist = candCy - currCy;
              secondaryDist = Math.abs(candCx - currCx);
            }
            break;
          case 'ArrowUp':
            if (candCy < currCy - 4) {
              isValid = true;
              primaryDist = currCy - candCy;
              secondaryDist = Math.abs(candCx - currCx);
            }
            break;
        }

        if (isValid) {
          // Weighted score: penalize perpendicular deviation heavily to stay in same row/column
          const score = primaryDist + secondaryDist * 2.8;
          if (score < minDistance) {
            minDistance = score;
            bestMatch = cand;
          }
        }
      }

      if (bestMatch) {
        bestMatch.focus();
        bestMatch.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
        return true;
      }

      return false;
    };

    const handleKeyDown = (e: KeyboardEvent) => {
      // 1. If Video Player is active, let VideoPlayer handle player keys
      const playerOpen = usePlayerStore.getState().isOpen;
      if (playerOpen) {
        if (['Escape', 'GoBack', 'BrowserBack'].includes(e.key) || e.keyCode === 4) {
          e.preventDefault();
          window.dispatchEvent(new CustomEvent('sv:hardware-back-from-player'));
        }
        return;
      }

      // 2. Hardware / Remote Back Button Handling
      if (['Escape', 'GoBack', 'BrowserBack'].includes(e.key) || e.keyCode === 4) {
        e.preventDefault();
        const ui = useUIStore.getState();

        if (ui.detailMedia) {
          ui.closeDetail();
          return;
        }

        if (ui.activeTab !== 'home') {
          ui.setActiveTab('home');
          return;
        }

        // Check if any modal is currently visible
        const openModalCloseBtn = document.querySelector<HTMLButtonElement>(
          '[role="dialog"] button[aria-label="Close"], [data-modal="true"] button[aria-label="Close"], [role="dialog"] .close-btn'
        );
        if (openModalCloseBtn) {
          openModalCloseBtn.click();
          return;
        }
        return;
      }

      // 3. Skip spatial navigation if user is typing into a text field
      if (isInputActive()) {
        return;
      }

      // 4. Directional D-Pad Navigation
      if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)) {
        const handled = navigateDirection(e.key as 'ArrowUp' | 'ArrowDown' | 'ArrowLeft' | 'ArrowRight');
        if (handled) {
          e.preventDefault();
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown, { passive: false });
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, []);
}


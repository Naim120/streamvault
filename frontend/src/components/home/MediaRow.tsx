import React, { useRef, useState, useEffect, useCallback } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { MediaItem } from '../../types';
import { PosterCard } from './PosterCard';

interface MediaRowProps {
  title: string;
  items: MediaItem[];
  isTop10?: boolean;
}

export const MediaRow: React.FC<MediaRowProps> = ({ title, items, isTop10 }) => {
  const rowRef = useRef<HTMLDivElement | null>(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(true);

  const checkScroll = useCallback(() => {
    if (!rowRef.current) return;
    const { scrollLeft, scrollWidth, clientWidth } = rowRef.current;
    setCanScrollLeft(scrollLeft > 15);
    setCanScrollRight(scrollLeft + clientWidth < scrollWidth - 15);
  }, []);

  useEffect(() => {
    checkScroll();
    const el = rowRef.current;
    if (el) {
      el.addEventListener('scroll', checkScroll, { passive: true });
      window.addEventListener('resize', checkScroll);
    }
    return () => {
      if (el) el.removeEventListener('scroll', checkScroll);
      window.removeEventListener('resize', checkScroll);
    };
  }, [items, checkScroll]);

  if (!items || items.length === 0) return null;

  const scroll = (direction: 'left' | 'right') => {
    if (!rowRef.current) return;
    const { scrollLeft, clientWidth } = rowRef.current;
    const scrollAmount = direction === 'left' ? -clientWidth * 0.75 : clientWidth * 0.75;
    rowRef.current.scrollTo({
      left: scrollLeft + scrollAmount,
      behavior: 'smooth'
    });
    setTimeout(checkScroll, 350);
  };

  return (
    <div className="py-4 space-y-3 relative group/row">
      <h2 className="text-white font-bold text-lg md:text-xl tracking-tight px-4 sm:px-8">
        {title}
      </h2>

      <div className="relative">
        {/* Left Scroll Arrow - only visible if scrolled to the right */}
        {canScrollLeft && (
          <button
            onClick={() => scroll('left')}
            aria-label="Scroll left"
            className="absolute left-0 top-0 bottom-0 z-30 w-10 sm:w-12 bg-black/60 hover:bg-black/90 text-white flex items-center justify-center opacity-0 group-hover/row:opacity-100 transition duration-200 cursor-pointer"
          >
            <ChevronLeft className="w-6 h-6 drop-shadow" />
          </button>
        )}

        {/* Carousel Container */}
        <div
          ref={rowRef}
          className="flex gap-3 sm:gap-4 overflow-x-auto no-scrollbar px-4 sm:px-8 py-2 scroll-smooth"
        >
          {items.map((item, idx) => (
            <PosterCard
              key={item.id}
              media={item}
              rank={isTop10 ? idx + 1 : undefined}
              isGrid={false}
            />
          ))}
        </div>

        {/* Right Scroll Arrow - only visible if content remains */}
        {canScrollRight && (
          <button
            onClick={() => scroll('right')}
            aria-label="Scroll right"
            className="absolute right-0 top-0 bottom-0 z-30 w-10 sm:w-12 bg-black/60 hover:bg-black/90 text-white flex items-center justify-center opacity-0 group-hover/row:opacity-100 transition duration-200 cursor-pointer"
          >
            <ChevronRight className="w-6 h-6 drop-shadow" />
          </button>
        )}
      </div>
    </div>
  );
};

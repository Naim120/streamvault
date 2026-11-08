import React from 'react';
import { PosterCard, PosterCardProps } from '../home/PosterCard';

export type MediaCardProps = PosterCardProps;

export const MediaCard: React.FC<MediaCardProps> = (props) => {
  return <PosterCard {...props} />;
};

export default MediaCard;


#!/usr/bin/env python3
"""Brighten sunrise and sunset icons for better visibility on dark backgrounds."""

from PIL import Image
import numpy as np

def brighten_image(input_path, output_path):
    """Make image pixels whiter while preserving transparency."""
    img = Image.open(input_path).convert('RGBA')
    data = np.array(img)
    
    # Make all non-transparent pixels much whiter
    # Increase RGB values significantly while keeping alpha channel
    mask = data[:,:,3] > 0  # Non-transparent pixels
    
    data[:,:,0] = np.where(mask, np.clip(data[:,:,0] * 1.8 + 100, 0, 255), data[:,:,0])  # R
    data[:,:,1] = np.where(mask, np.clip(data[:,:,1] * 1.8 + 100, 0, 255), data[:,:,1])  # G
    data[:,:,2] = np.where(mask, np.clip(data[:,:,2] * 1.8 + 100, 0, 255), data[:,:,2])  # B
    
    img_brightened = Image.fromarray(data.astype('uint8'), 'RGBA')
    img_brightened.save(output_path)
    print(f"Brightened {input_path} -> {output_path}")

if __name__ == "__main__":
    brighten_image('static/sunrise.png', 'static/sunrise.png')
    brighten_image('static/sunset.png', 'static/sunset.png')
    print("Images brightened successfully!")

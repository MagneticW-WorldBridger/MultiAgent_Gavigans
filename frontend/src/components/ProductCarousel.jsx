/**
 * ProductCarousel - Horizontal scrolling product display
 */
import { useRef, useState, useEffect } from 'react'
import { ChevronLeft, ChevronRight, ExternalLink, ShoppingBag } from 'lucide-react'

function ProductCard({ product }) {
  const imageUrl = product.image_url || product.imageUrl
  const hasImage = imageUrl && !imageUrl.includes('no_selection')
  const [imgError, setImgError] = useState(false)
  
  return (
    <div className="product-card">
      {hasImage && !imgError ? (
        <img 
          src={imageUrl} 
          alt={product.name}
          className="product-card-image"
          loading="lazy"
          onError={() => setImgError(true)}
        />
      ) : (
        <div className="product-card-placeholder">
          <ShoppingBag className="w-8 h-8" />
        </div>
      )}
      
      <div className="product-card-body">
        <h4 className="product-card-title">{product.name}</h4>
        <p className="product-card-price">
          {product.price_label || (product.price ? `Starting at $${product.price}` : 'Contact Store for Pricing')}
        </p>
        {product.url && (
          <a 
            href={product.url} 
            target="_blank" 
            rel="noopener noreferrer"
            className="product-card-link"
          >
            View <ExternalLink className="w-3 h-3" />
          </a>
        )}
      </div>
    </div>
  )
}

export function ProductCarousel({ products }) {
  const scrollRef = useRef(null)
  const [canScrollLeft, setCanScrollLeft] = useState(false)
  const [canScrollRight, setCanScrollRight] = useState(true)

  const checkScroll = () => {
    if (scrollRef.current) {
      const { scrollLeft, scrollWidth, clientWidth } = scrollRef.current
      setCanScrollLeft(scrollLeft > 0)
      setCanScrollRight(scrollLeft < scrollWidth - clientWidth - 10)
    }
  }

  const scroll = (direction) => {
    if (scrollRef.current) {
      const scrollAmount = 200
      scrollRef.current.scrollBy({
        left: direction === 'left' ? -scrollAmount : scrollAmount,
        behavior: 'smooth'
      })
    }
  }

  useEffect(() => {
    checkScroll()
    const el = scrollRef.current
    if (el) {
      el.addEventListener('scroll', checkScroll)
      return () => el.removeEventListener('scroll', checkScroll)
    }
  }, [products])

  if (!products?.length) return null

  return (
    <div className="carousel-container">
      <button 
        className="carousel-btn carousel-btn-prev" 
        onClick={() => scroll('left')}
        disabled={!canScrollLeft}
        aria-label="Scroll left"
      >
        <ChevronLeft className="w-4 h-4" />
      </button>
      
      <div className="carousel-viewport">
        <div ref={scrollRef} className="carousel-track">
          {products.slice(0, 10).map((product, idx) => (
            <ProductCard key={product.sku || idx} product={product} />
          ))}
        </div>
      </div>
      
      <button 
        className="carousel-btn carousel-btn-next" 
        onClick={() => scroll('right')}
        disabled={!canScrollRight}
        aria-label="Scroll right"
      >
        <ChevronRight className="w-4 h-4" />
      </button>
    </div>
  )
}

export default ProductCarousel





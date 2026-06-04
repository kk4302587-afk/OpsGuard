import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import '../styles/landing.css'

interface LandingPageProps {
  onEnter: () => void
}

/**
 * Premium 3D Landing Page for OpsGuard - Option B with Ultimate Enhancements.
 * Features:
 *  - Rotating wireframe cyber-shield and orbital rings with satellite data packets.
 *  - Falling particle storm with dynamic physics deflection.
 *  - Spark splash effects generated at collision points on the shield.
 *  - Pulse/Breathing animation on the core node.
 *  - Mouse magnetic force field repelling threat particles and tilting the shield.
 */
function LandingPage({ onEnter }: LandingPageProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mouseRef = useRef({ x: 0, y: 0, targetX: 0, targetY: 0 })
  const [isExiting, setIsExiting] = useState(false)
  const isBtnHoveredRef = useRef(false)

  const handleStart = () => {
    setIsExiting(true)
    setTimeout(() => {
      onEnter()
    }, 600) // matches CSS landing-fade-out transition
  }

  useEffect(() => {
    if (!containerRef.current) return

    const container = containerRef.current
    const width = container.clientWidth
    const height = container.clientHeight

    // 1. Scene, Camera, and WebGLRenderer Setup
    const scene = new THREE.Scene()
    scene.fog = new THREE.FogExp2(0xe2e8f0, 0.0035) // Light fog matching ice-blue gradient

    const camera = new THREE.PerspectiveCamera(55, width / height, 1, 1000)
    camera.position.z = 210

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    renderer.setSize(width, height)
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    container.appendChild(renderer.domElement)

    const clock = new THREE.Clock()

    // 2. Shield Group Setup (Holds all shield elements)
    const shieldGroup = new THREE.Group()
    scene.add(shieldGroup)

    // Set shield position to origin (0, 0, 0)
    shieldGroup.position.x = 0

    const shieldRadius = 70 // Perfect size for 55% screen width canvas split

    // A. Outer Geometric Grid Shield (Icosahedron)
    const shieldGeo = new THREE.IcosahedronGeometry(shieldRadius, 2)
    const shieldWireMat = new THREE.LineBasicMaterial({
      color: 0x059669, // High contrast emerald green
      transparent: true,
      opacity: 0.42,
      blending: THREE.NormalBlending,
      depthWrite: false,
    })
    const shieldWire = new THREE.LineSegments(
      new THREE.WireframeGeometry(shieldGeo),
      shieldWireMat
    )
    shieldGroup.add(shieldWire)

    // B. Inner Core Node (glowing particle sphere)
    const coreGeo = new THREE.IcosahedronGeometry(shieldRadius - 26, 1)
    const corePointsMat = new THREE.PointsMaterial({
      size: 3.5,
      color: 0x10b981, // Vibrant emerald core
      transparent: true,
      opacity: 0.8,
      blending: THREE.NormalBlending,
    })
    const corePoints = new THREE.Points(coreGeo, corePointsMat)
    shieldGroup.add(corePoints)

    // C. Concentric Orbital Rings (Torus)
    const ringMat = new THREE.LineBasicMaterial({
      color: 0x0284c7, // Sky blue rings
      transparent: true,
      opacity: 0.35,
      blending: THREE.NormalBlending,
    })

    const ringGeoA = new THREE.TorusGeometry(shieldRadius + 14, 0.5, 8, 64)
    const ringA = new THREE.LineSegments(new THREE.WireframeGeometry(ringGeoA), ringMat)
    ringA.rotation.x = Math.PI / 4
    shieldGroup.add(ringA)

    const ringGeoB = new THREE.TorusGeometry(shieldRadius + 22, 0.5, 8, 64)
    const ringB = new THREE.LineSegments(new THREE.WireframeGeometry(ringGeoB), ringMat)
    ringB.rotation.y = Math.PI / 3
    shieldGroup.add(ringB)

    // D. Orbital Ring Satellites (流星光斑数据包)
    const satelliteGeo = new THREE.SphereGeometry(2.0, 12, 12)
    const satelliteMatA = new THREE.MeshBasicMaterial({
      color: 0x0ea5e9, // Bright blue satellite
      transparent: true,
      opacity: 0.9,
      blending: THREE.AdditiveBlending,
    })
    const satelliteMatB = new THREE.MeshBasicMaterial({
      color: 0x10b981, // Bright green satellite
      transparent: true,
      opacity: 0.9,
      blending: THREE.AdditiveBlending,
    })

    const satelliteA = new THREE.Mesh(satelliteGeo, satelliteMatA)
    ringA.add(satelliteA)

    const satelliteB = new THREE.Mesh(satelliteGeo, satelliteMatB)
    ringB.add(satelliteB)

    // 3. Falling Particle Threat Storm (blocked and deflected by shield)
    const threatCount = 200
    const threatGeo = new THREE.BufferGeometry()
    const positions = new Float32Array(threatCount * 3)

    // Track particle states individually for physics calculation
    const particlePositions: THREE.Vector3[] = []
    const particleVelocities: THREE.Vector3[] = []

    const spawnParticle = (index: number, atTop = false) => {
      const shieldX = shieldGroup.position.x
      const x = shieldX + (Math.random() - 0.5) * 260
      const y = atTop ? 140 : (Math.random() - 0.5) * 240
      const z = (Math.random() - 0.5) * 160

      const pos = new THREE.Vector3(x, y, z)
      const vel = new THREE.Vector3(0, -(Math.random() * 0.9 + 0.6), 0) // Falling downward

      particlePositions[index] = pos
      particleVelocities[index] = vel

      positions[index * 3] = pos.x
      positions[index * 3 + 1] = pos.y
      positions[index * 3 + 2] = pos.z
    }

    // Populate initial particles
    for (let i = 0; i < threatCount; i++) {
      spawnParticle(i, false)
    }

    threatGeo.setAttribute('position', new THREE.BufferAttribute(positions, 3))

    const threatMat = new THREE.PointsMaterial({
      size: 3.2,
      color: 0xdc2626, // Crimson red threats
      transparent: true,
      opacity: 0.85,
      blending: THREE.NormalBlending,
      depthWrite: false,
    })
    const threatPoints = new THREE.Points(threatGeo, threatMat)
    scene.add(threatPoints)

    // 4. Deflection Spark System (碰撞火花粒子池)
    const maxSparks = 80
    const sparkGeo = new THREE.BufferGeometry()
    const sparkPositions = new Float32Array(maxSparks * 3)
    
    interface Spark {
      pos: THREE.Vector3
      vel: THREE.Vector3
      life: number
      decay: number
    }
    const sparks: Spark[] = []

    // Prefill the spark positions and list
    for (let i = 0; i < maxSparks; i++) {
      sparks.push({
        pos: new THREE.Vector3(9999, 9999, 9999), // Positioned offscreen initially
        vel: new THREE.Vector3(),
        life: 0,
        decay: 0
      })
      sparkPositions[i * 3] = 9999
      sparkPositions[i * 3 + 1] = 9999
      sparkPositions[i * 3 + 2] = 9999
    }

    sparkGeo.setAttribute('position', new THREE.BufferAttribute(sparkPositions, 3))
    const sparkMat = new THREE.PointsMaterial({
      size: 2.2,
      color: 0x10b981, // Glowing emerald green sparks on deflection
      transparent: true,
      opacity: 0.9,
      blending: THREE.AdditiveBlending,
      depthWrite: false
    })
    const sparkPoints = new THREE.Points(sparkGeo, sparkMat)
    scene.add(sparkPoints)

    // Function to trigger deflection splash
    const spawnSparks = (origin: THREE.Vector3, normal: THREE.Vector3) => {
      let spawned = 0
      const countToSpawn = 3 + Math.floor(Math.random() * 3) // 3 to 5 sparks per hit
      for (let i = 0; i < maxSparks; i++) {
        const s = sparks[i]
        if (s.life <= 0) {
          s.pos.copy(origin)
          // Direction: mainly along normal vector with random dispersion
          s.vel.copy(normal).multiplyScalar(Math.random() * 1.6 + 1.2)
          s.vel.x += (Math.random() - 0.5) * 1.0
          s.vel.y += (Math.random() - 0.5) * 1.0
          s.vel.z += (Math.random() - 0.5) * 1.0

          s.life = 1.0
          s.decay = 0.035 + Math.random() * 0.03

          spawned++
          if (spawned >= countToSpawn) break
        }
      }
    }

    // 5. Mouse movement tracking
    const handleMouseMove = (e: MouseEvent) => {
      mouseRef.current.targetX = (e.clientX / window.innerWidth) * 2 - 1
      mouseRef.current.targetY = -(e.clientY / window.innerHeight) * 2 + 1
    }
    window.addEventListener('mousemove', handleMouseMove)

    // 6. Animation Render Loop
    let animationFrameId: number
    const animate = () => {
      animationFrameId = requestAnimationFrame(animate)

      const time = clock.getElapsedTime()

      // Rotate shield elements on different axes
      shieldWire.rotation.y += 0.0012
      shieldWire.rotation.x += 0.0004

      corePoints.rotation.y -= 0.0015 // Counter rotate core

      ringA.rotation.z += 0.002
      ringB.rotation.z -= 0.0015

      // A. Satellite movement sliding along orbital rings
      const angleA = time * 1.6
      satelliteA.position.x = Math.cos(angleA) * (shieldRadius + 14)
      satelliteA.position.y = Math.sin(angleA) * (shieldRadius + 14)

      const angleB = -time * 1.2
      satelliteB.position.x = Math.cos(angleB) * (shieldRadius + 22)
      satelliteB.position.z = Math.sin(angleB) * (shieldRadius + 22)

      // B. Core energy breathing / pulsing effect
      const breatheVal = Math.sin(time * 3.5)
      corePointsMat.size = 3.0 + breatheVal * 0.8
      corePointsMat.opacity = 0.65 + breatheVal * 0.15

      // C. Shield rotation tilt based on mouse position
      shieldGroup.rotation.x = mouseRef.current.y * 0.22
      shieldGroup.rotation.y = -0.22 + mouseRef.current.x * 0.22

      // D. Threat particles movement & mouse repulsion力场
      const posAttr = threatPoints.geometry.attributes.position as THREE.BufferAttribute
      const posArray = posAttr.array as Float32Array
      const shieldCenter = shieldGroup.position

      // Compute projected mouse 3D vector for proximity deflection
      const mouse3D = new THREE.Vector3(
        mouseRef.current.x * 110 + shieldCenter.x,
        mouseRef.current.y * 110,
        0
      )

      for (let i = 0; i < threatCount; i++) {
        const pos = particlePositions[i]
        const vel = particleVelocities[i]

        // Mouse force field repulsion check
        const distToMouse = pos.distanceTo(mouse3D)
        if (distToMouse < 45) {
          const repelDir = pos.clone().sub(mouse3D).normalize()
          const force = (45 - distToMouse) * 0.016
          vel.x += repelDir.x * force
          vel.z += repelDir.z * force
        }

        // Apply velocity
        pos.add(vel)

        // Boundary check: reset to top if particle falls below container limit
        if (pos.y < -130) {
          spawnParticle(i, true)
          continue
        }

        // Collision Check: distance from dynamic shield center
        const dist = pos.distanceTo(shieldCenter)
        if (dist < shieldRadius) {
          const normal = pos.clone().sub(shieldCenter).normalize()

          // Push particle exactly to the boundary
          pos.copy(normal).multiplyScalar(shieldRadius).add(shieldCenter)

          // Bounce velocity with reflection
          vel.reflect(normal).multiplyScalar(0.35)

          vel.x += normal.x * 0.15
          vel.z += normal.z * 0.15

          // Trigger energy spark splashes at the boundary collision point
          spawnSparks(pos, normal)
        }

        posArray[i * 3] = pos.x
        posArray[i * 3 + 1] = pos.y
        posArray[i * 3 + 2] = pos.z
      }
      posAttr.needsUpdate = true

      // E. Update sparks pool and buffer array
      const sparkPosArray = sparkGeo.attributes.position.array as Float32Array
      for (let i = 0; i < maxSparks; i++) {
        const s = sparks[i]
        if (s.life > 0) {
          s.pos.add(s.vel)
          s.vel.multiplyScalar(0.93) // deceleration
          s.life -= s.decay

          sparkPosArray[i * 3] = s.pos.x
          sparkPosArray[i * 3 + 1] = s.pos.y
          sparkPosArray[i * 3 + 2] = s.pos.z
        } else {
          sparkPosArray[i * 3] = 9999
          sparkPosArray[i * 3 + 1] = 9999
          sparkPosArray[i * 3 + 2] = 9999
        }
      }
      sparkGeo.attributes.position.needsUpdate = true

      // F. Camera parallax centered around the shield (maintains constant distance to shield center)
      mouseRef.current.x += (mouseRef.current.targetX - mouseRef.current.x) * 0.05
      mouseRef.current.y += (mouseRef.current.targetY - mouseRef.current.y) * 0.05

      const distance = 210
      const angleX = mouseRef.current.x * 0.45 // horizontal rotation angle
      const angleY = mouseRef.current.y * 0.40 // vertical rotation angle

      camera.position.x = shieldCenter.x + Math.sin(angleX) * Math.cos(angleY) * distance
      camera.position.y = shieldCenter.y + Math.sin(angleY) * distance
      camera.position.z = shieldCenter.z + Math.cos(angleX) * Math.cos(angleY) * distance
      camera.lookAt(shieldCenter)

      renderer.render(scene, camera)
    }

    animate()

    // 7. Resizing handler
    const handleResize = () => {
      if (!containerRef.current) return
      const w = containerRef.current.clientWidth
      const h = containerRef.current.clientHeight
      camera.aspect = w / h
      camera.updateProjectionMatrix()
      renderer.setSize(w, h)

      shieldGroup.position.x = 0
    }
    window.addEventListener('resize', handleResize)

    // Helper to safely dispose materials (handling arrays of materials)
    const disposeMaterial = (mat: any) => {
      if (!mat) return
      if (Array.isArray(mat)) {
        mat.forEach((m) => m.dispose?.())
      } else {
        mat.dispose?.()
      }
    }

    // 8. Cleanup resources
    return () => {
      cancelAnimationFrame(animationFrameId)
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('resize', handleResize)

      if (container.contains(renderer.domElement)) {
        container.removeChild(renderer.domElement)
      }

      // Dispose Geometries and Materials
      shieldGeo.dispose()
      disposeMaterial(shieldWireMat)

      coreGeo.dispose()
      disposeMaterial(corePointsMat)

      ringGeoA.dispose()
      ringGeoB.dispose()
      disposeMaterial(ringMat)

      satelliteGeo.dispose()
      disposeMaterial(satelliteMatA)
      disposeMaterial(satelliteMatB)

      threatGeo.dispose()
      disposeMaterial(threatMat)

      sparkGeo.dispose()
      disposeMaterial(sparkMat)

      renderer.dispose()
    }
  }, [])

  return (
    <div className={`landing-container ${isExiting ? 'landing-fade-out' : ''}`}>
      {/* 3D WebGL Canvas */}
      <div ref={containerRef} className="landing-canvas" />

      {/* Floating Glassmorphism Container */}
      <div className="landing-card">
        <h1 className="landing-title">OpsGuard 智能运维卫士</h1>
        <p className="landing-subtitle">
          基于大语言模型与多模态感知技术的企业级智能安全运维协同控制台。
          提供自动化风险研判、深度知识检索与智能安全屏障，全方位守护您的核心云端资产安全。
        </p>
        <button
          className="landing-btn"
          onMouseEnter={() => { isBtnHoveredRef.current = true }}
          onMouseLeave={() => { isBtnHoveredRef.current = false }}
          onClick={handleStart}
        >
          开启智能运维
        </button>
      </div>
    </div>
  )
}

export default LandingPage
